"""Static, stdout-only Windows service diagnosis; never import integration code.

Run with a trusted Python: -I -S -B. This cannot inspect the service's Python heap,
so even a successful result is an on-disk candidate, never runtime attestation.
"""
import argparse
import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
SERVICE = 'ERPNextBiometricPushService'
DEVICES = ('FP2_DEVICE_08', 'FP2_DEVICE_11')
MAX_SOURCE = 1024 * 1024
# Reviewed source at 478be326. Normalize only CRLF, so Git newline settings do
# not change the result. Unknown code is reported, never silently trusted.
REVIEWED = {
    'erpnext_sync.py': '8249a90ade7a049ffe781df808c67369b7948da4184864cc415102795e7706bc',
    'erpnext_sync_win.py': '54577b7c158dff51a0028a8a1449456c29d295a81b1216b010b0400e701ec6ea',
    'SMWinservice.py': '2b0961cc95aef459eea4b8add413406bb79e1022160d254695aa82912ac2cb21',
    'punch_normalization.py': 'a70d7eebb2e58cf46a31c9872aa8480e24f0c1589ce53bfc52dc358279cb44d8',
    'durable_sync.py': 'd3fd811dc604e3dac1647116d957c10fc230caa0d5dbb5bb6a9254459fd09713',
    'durable_delivery.py': 'f4c005c5be0c3d6ceed31815f0c62589ed70ffdef3e6ec355b0d1f2168b82531',
}
# Separately reviewed production source. Never replace the development profile
# with production hashes or accept a mixture as one verified deployment.
PRODUCTION_REVIEWED = {
    'erpnext_sync.py': '1353b5a008f91bad033f0114b7da494bb7ba62d9722a260d2032da7c611191ba',
    'erpnext_sync_win.py': 'bffb7d31f6270ab40d996060e4ec6e4df2250a583749f59e20c97c76eea9b257',
    'SMWinservice.py': REVIEWED['SMWinservice.py'],
}
SETTING_NAMES = ('devices', 'device_punch_values_IN', 'device_punch_values_OUT',
                 'ERPNEXT_VERSION', 'ERPNEXT_URL')

# Fixed local queries only. Never return account names, full environments, or
# arbitrary command lines to stdout: the Python layer retains them privately.
WINDOWS_QUERY = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$expectedRoot = [Environment]::GetEnvironmentVariable('FPF_DIAGNOSTIC_ROOT')
function Test-ReviewedLauncher([string]$command) {
    # Return a boolean only; never emit the command or use it as executable code.
    if ([string]::IsNullOrEmpty($command)) { return $false }
    $pattern = '^\s*(?:"([^"\r\n]+)"|([^\s"\r\n]+))\s+(?:"([^"\r\n]+)"|([^\s"\r\n]+))\s*$'
    $match = [regex]::Match($command, $pattern)
    if (-not $match.Success) { return $false }
    $exe = $match.Groups[1].Value
    if (-not $exe) { $exe = $match.Groups[2].Value }
    $script = $match.Groups[3].Value
    if (-not $script) { $script = $match.Groups[4].Value }
    if (-not [IO.Path]::IsPathRooted($exe) -or -not [IO.Path]::IsPathRooted($script)) { return $false }
    if ([IO.Path]::GetFileName($exe) -notin @('python.exe','pythonw.exe')) { return $false }
    return [string]::Equals([IO.Path]::GetFullPath($script), (Join-Path $expectedRoot 'erpnext_sync_win.py'), [StringComparison]::OrdinalIgnoreCase)
}
$s = Get-CimInstance -ClassName Win32_Service -Filter "Name='ERPNextBiometricPushService'"
if ($null -eq $s) { throw 'service unavailable' }
$p = $null
if ($s.ProcessId -gt 0) {
    $p = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId=" + $s.ProcessId)
}
$serviceKey = Get-Item -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\ERPNextBiometricPushService'
$serviceEnv = @($serviceKey.GetValue('Environment'))
$flags = @{}
foreach ($key in @('PYTHONPATH','PYTHONHOME','TZ')) {
    $flags[$key] = [pscustomobject]@{
        machine = ($null -ne [Environment]::GetEnvironmentVariable($key, 'Machine'))
        service = (@($serviceEnv | Where-Object { $_ -match ('^' + $key + '=') }).Count -gt 0)
        diagnostic_process = ($null -ne [Environment]::GetEnvironmentVariable($key, 'Process'))
    }
}
$z = Get-TimeZone
$started = $null
if ($null -ne $p -and $null -ne $p.CreationDate) { $started = $p.CreationDate.ToUniversalTime().ToString('o') }
[pscustomobject]@{
    state = $s.State; pid = [int]$s.ProcessId; started_utc = $started
    registration_matches_selected_root = (Test-ReviewedLauncher $s.PathName)
    process_command_matches_selected_root = (Test-ReviewedLauncher $p.CommandLine)
    timezone_id = $z.Id; utc_offset_minutes = [int]$z.GetUtcOffset([DateTime]::Now).TotalMinutes
    override_flags = $flags
} | ConvertTo-Json -Depth 5 -Compress
"""


class DiagnosticError(Exception):
    """Only fixed codes escape this module; syntax errors can contain secrets."""


def require(condition, code):
    if not condition:
        raise DiagnosticError(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_source(root, name):
    path = root / name
    # No alternate locations, symlink escapes, retry dumps, or directory scans.
    require(path.resolve().parent == root and path.is_file(), 'SOURCE_NOT_LOCAL_REGULAR_FILE')
    require(path.stat().st_size <= MAX_SOURCE, 'SOURCE_TOO_LARGE')
    data = path.read_bytes()
    require(len(data) <= MAX_SOURCE, 'SOURCE_TOO_LARGE')
    return data, path.stat().st_mtime


def literal_node(node):
    """Check syntax without evaluating unrelated secret-bearing assignments."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(literal_node(x) for x in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and literal_node(k) and literal_node(v)
                   for k, v in zip(node.keys, node.values))
    return (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub))
            and isinstance(node.operand, ast.Constant)
            and type(node.operand.value) in (int, float))


def inspect_config(data):
    """Report declarations, not executed values. Never resolve names or defaults.

    An executable construct anywhere could mutate globals indirectly. Such a
    module remains unresolved, even when some assignments look literal. This
    deliberate limitation is safer than attempting to interpret Python.
    """
    tree = ast.parse(data.decode('utf-8-sig'))
    assignments = {key: [] for key in SETTING_NAMES}
    module_dynamic = False

    def inert_expression(node):
        # A bare name is unresolved but cannot itself call code. Containers may
        # include names; calls, attribute/subscript lookup and unpacking cannot.
        if literal_node(node) or isinstance(node, ast.Name):
            return True
        if isinstance(node, (ast.List, ast.Tuple)):
            return all(inert_expression(x) for x in node.elts)
        if isinstance(node, ast.Dict):
            return all(k is not None and inert_expression(k) and inert_expression(v)
                       for k, v in zip(node.keys, node.values))
        return False

    for node in tree.body:
        if isinstance(node, ast.Pass) or (isinstance(node, ast.Expr) and literal_node(node.value)):
            continue
        if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
            for target in node.targets:
                if target.id in assignments:
                    assignments[target.id].append(node.value if len(node.targets) == 1 else None)
            if not inert_expression(node.value):
                module_dynamic = True
        else:
            # Imports, conditionals, definitions, mutation and executable code
            # can change even apparently absent settings. Do not guess reachability.
            module_dynamic = True

    def entry(state, present=None, value=None, include_value=False, reason=None):
        item = {'state': state, 'present': present,
                'confidence': 'on_disk_declaration_only' if state in ('literal', 'absent') else 'unresolved'}
        if include_value:
            item['value'] = value
        if reason:
            item['reason'] = reason
        return item

    def declaration(key):
        nodes = assignments[key]
        if module_dynamic:
            return entry('dynamic', reason='MODULE_MAY_OVERRIDE_SETTINGS'), None
        if not nodes:
            return entry('absent', False), None
        if len(nodes) != 1 or nodes[0] is None:
            return entry('ambiguous', reason='ASSIGNMENT_OVERRIDE_OR_ALIAS'), None
        return None, nodes[0]

    settings = {}
    for key in SETTING_NAMES[1:]:
        result, node = declaration(key)
        output_key = 'erpnext_url_matches_approved_site' if key == 'ERPNEXT_URL' else key
        if result is None:
            if not literal_node(node):
                result = entry('dynamic', True, reason='NON_LITERAL_VALUE')
            else:
                # Only evaluate the requested scalar/list. Never evaluate devices
                # as a dictionary or unrelated credential-bearing assignments.
                value = ast.literal_eval(node)
                if key == 'ERPNEXT_URL':
                    if isinstance(value, str):
                        result = entry('literal', True, value in (
                            'https://erp.myfpf.com', 'https://erp.myfpf.com/',
                            'https://myfpf.v.frappe.cloud', 'https://myfpf.v.frappe.cloud/'), True)
                    else:
                        result = entry('invalid_literal', True, reason='VALUE_WITHHELD')
                elif key == 'ERPNEXT_VERSION':
                    result = (entry('literal', True, value, True) if type(value) is int and 12 <= value <= 99
                              else entry('invalid_literal', True, reason='VALUE_WITHHELD'))
                else:
                    valid = (isinstance(value, (list, tuple)) and len(value) <= 256
                             and all(type(x) is int and 0 <= x <= 255 for x in value))
                    result = (entry('literal', True, list(value), True) if valid
                              else entry('invalid_literal', True, reason='VALUE_WITHHELD'))
        settings[output_key] = result

    devices_result, devices_node = declaration('devices')
    if devices_result is not None:
        for device in DEVICES:
            settings[device + '.punch_direction'] = dict(devices_result, reason='DEVICES_' + devices_result['state'].upper())
        return settings
    if not isinstance(devices_node, (ast.List, ast.Tuple)):
        for device in DEVICES:
            settings[device + '.punch_direction'] = entry('dynamic', reason='DEVICE_INVENTORY_UNRESOLVED')
        return settings

    found = {device: [] for device in DEVICES}
    inventory_uncertain = False
    for device in devices_node.elts:
        if not isinstance(device, ast.Dict):
            inventory_uncertain = True
            continue
        fields = {}
        for key, value in zip(device.keys, device.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                inventory_uncertain = True
                continue
            fields.setdefault(key.value, []).append(value)
        identifiers = fields.get('device_id', [])
        if len(identifiers) != 1 or not isinstance(identifiers[0], ast.Constant) or not isinstance(identifiers[0].value, str):
            inventory_uncertain = True
            continue
        identifier = identifiers[0].value
        if identifier in found:
            found[identifier].append(fields.get('punch_direction', []))
    for device, matches in found.items():
        if inventory_uncertain:
            result = entry('ambiguous', reason='DEVICE_INVENTORY_MAY_CONTAIN_OVERRIDES')
        elif len(matches) > 1:
            result = entry('ambiguous', reason='DUPLICATE_DEVICE_ID')
        elif not matches:
            result = entry('absent', False, reason='DEVICE_ENTRY_ABSENT')
        elif not matches[0]:
            result = entry('absent', False, reason='DIRECTION_FIELD_ABSENT')
        elif len(matches[0]) != 1:
            result = entry('ambiguous', reason='DUPLICATE_DIRECTION_FIELD')
        elif not literal_node(matches[0][0]):
            result = entry('dynamic', True, reason='NON_LITERAL_VALUE')
        else:
            value = ast.literal_eval(matches[0][0])
            valid = value is None or isinstance(value, str) and value in ('AUTO', 'IN', 'OUT')
            result = (entry('literal', True, value, True) if valid
                      else entry('invalid_literal', True, reason='VALUE_WITHHELD'))
        settings[device + '.punch_direction'] = result
    return settings


def config_policy(data):
    tree = ast.parse(data.decode('utf-8-sig'))
    assignments = {}
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue  # Module docstrings are inert.
        require(isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and literal_node(node.value),
                'CONFIG_EXECUTABLE_OR_DYNAMIC_CONTENT')
        key = node.targets[0].id
        require(key not in assignments, 'CONFIG_REASSIGNMENT_DETECTED')
        assignments[key] = node.value

    def value(key, default):
        return ast.literal_eval(assignments[key]) if key in assignments else default

    enabled = value('DURABLE_SYNC_ENABLED', False)
    version = value('ERPNEXT_VERSION', 14)
    require(type(enabled) is bool, 'INVALID_SENDING_PATH_FLAG')
    require(type(version) is int and 12 <= version <= 99, 'INVALID_ENDPOINT_VERSION')
    # Print only an exact approved site match, never a URL containing credentials,
    # query strings, unexpected paths, or attacker-controlled hostnames.
    site = value('ERPNEXT_URL', None)
    require(site in ('https://erp.myfpf.com', 'https://erp.myfpf.com/',
                     'https://myfpf.v.frappe.cloud', 'https://myfpf.v.frappe.cloud/'),
            'DESTINATION_NOT_APPROVED_EXACT_SITE')
    codes = {}
    for key, default in (('device_punch_values_IN', [0, 4]), ('device_punch_values_OUT', [1, 5])):
        items = value(key, default)
        require(isinstance(items, (list, tuple)) and len(items) <= 256
                and all(type(x) is int and 0 <= x <= 255 for x in items), 'INVALID_DIRECTION_CODES')
        codes[key] = list(items)
    devices = assignments.get('devices')
    require(isinstance(devices, (ast.List, ast.Tuple)), 'LITERAL_DEVICES_REQUIRED')
    policies, seen = {}, set()
    for device in devices.elts:
        require(isinstance(device, ast.Dict), 'LITERAL_DEVICES_REQUIRED')
        fields = {}
        for key, item in zip(device.keys, device.values):
            require(isinstance(key, ast.Constant) and isinstance(key.value, str), 'INVALID_DEVICE_FIELD')
            require(key.value not in fields, 'DUPLICATE_DEVICE_FIELD')
            fields[key.value] = item
        identifier = ast.literal_eval(fields['device_id'])
        require(isinstance(identifier, str) and identifier not in seen, 'DUPLICATE_OR_INVALID_DEVICE_ID')
        seen.add(identifier)
        if identifier in DEVICES:
            direction = ast.literal_eval(fields['punch_direction']) if 'punch_direction' in fields else None
            require(direction is None or type(direction) is str and direction in ('AUTO', 'IN', 'OUT'),
                    'INVALID_PUNCH_DIRECTION')
            policies[identifier] = {'punch_direction': direction,
                                    'direction_explicit': 'punch_direction' in fields}
    require(set(policies) == set(DEVICES), 'AFFECTED_DEVICE_MISSING')
    return {'candidate_sending_path': 'durable' if enabled else 'legacy',
            'candidate_endpoint_method': ('hrms' if version > 13 else 'erpnext')
            + '.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field',
            'erpnext_url_matches_approved_site': True, 'device_policies': policies, **codes,
            'defaulted_settings': [k for k in ('DURABLE_SYNC_ENABLED', 'ERPNEXT_VERSION',
                'device_punch_values_IN', 'device_punch_values_OUT') if k not in assignments]}


def service_snapshot(root):
    require(os.name == 'nt', 'WINDOWS_REQUIRED')
    # An absolute executable and -NoProfile avoid PATH lookup and profile scripts.
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    require(ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer)) > 0, 'SYSTEM_DIRECTORY_UNAVAILABLE')
    powershell = Path(buffer.value) / 'WindowsPowerShell/v1.0/powershell.exe'
    environment = os.environ.copy()
    # The only added environment value is a non-secret directory, not config.
    # Do not interpolate it into executable PowerShell or append service arguments.
    environment['FPF_DIAGNOSTIC_ROOT'] = str(root)
    result = subprocess.run([str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive',
                             '-Command', WINDOWS_QUERY], capture_output=True, timeout=30,
                            env=environment)
    require(result.returncode == 0 and len(result.stdout) <= 65536, 'LOCAL_SERVICE_QUERY_FAILED')
    # The fixed PowerShell script sets UTF-8, including for non-ASCII paths.
    return json.loads(result.stdout.decode('utf-8-sig'))


def checkout_revision(root):
    """Read Git metadata as data, never run Git/hooks or print branch/path values.

    HEAD identifies the on-disk checkout, not dirty files or the loaded service.
    Linked worktrees and external metadata paths stay explicitly unavailable.
    """
    result = {'commit': None, 'scope': 'on_disk_checkout_metadata_only',
              'loaded_service_revision_verified': False, 'files_match_commit_verified': False}
    git = root / '.git'
    if not git.is_dir() or git.is_symlink() or git.resolve() != git:
        return dict(result, status='GIT_METADATA_UNAVAILABLE_OR_EXTERNAL')

    def read_metadata(path):
        resolved = path.resolve()
        require(git in resolved.parents and path.is_file() and path.stat().st_size <= MAX_SOURCE,
                'GIT_METADATA_UNSUPPORTED')
        return path.read_text(encoding='ascii').strip()

    try:
        head = read_metadata(git / 'HEAD')
        if head.startswith('ref: '):
            reference = head[5:]
            require(reference.startswith('refs/') and all(
                re.fullmatch(r'[A-Za-z0-9_.-]+', part) and part not in ('.', '..')
                for part in reference.split('/')), 'GIT_REFERENCE_UNSUPPORTED')
            path = git / reference
            if path.is_file():
                head = read_metadata(path)
            else:
                matches = [line.split(' ')[0] for line in read_metadata(git / 'packed-refs').splitlines()
                           if line.endswith(' ' + reference)]
                require(len(matches) == 1, 'GIT_REFERENCE_UNAVAILABLE')
                head = matches[0]
        require(re.fullmatch(r'(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})', head), 'GIT_COMMIT_INVALID')
        return dict(result, commit=head.lower(), status='CHECKOUT_COMMIT_OBSERVED_NOT_LOADED_ATTESTATION')
    except Exception:
        return dict(result, status='GIT_METADATA_UNAVAILABLE_OR_UNSUPPORTED')


def diagnose(root, inspect_service=None, settings_only=False):
    root = Path(root).resolve(strict=True)
    if inspect_service is None:
        inspect_service = lambda: service_snapshot(root)
    report = {'schema': 'windows-integration-static-diagnostic-v1',
              'observed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'reviewed_source_revision': '478be326ff35fbc8a62a150866eb976b6a658544',
              'service': SERVICE, 'loaded_runtime_configuration_verified': False,
              'production_server_semantics_verified': False,
              'read_only': True, 'findings': [], 'sources': {}}
    before = inspect_service()
    revision_before = checkout_revision(root)
    report['deployed_checkout_revision'] = revision_before
    # Raw SCM/process command lines are deliberately NOT copied into the report.
    report['service_observation'] = {key: before.get(key) for key in ('state', 'pid', 'started_utc')}
    report['registration_matches_selected_root'] = before.get('registration_matches_selected_root') is True
    report['process_command_matches_selected_root'] = before.get('process_command_matches_selected_root') is True
    report['timezone'] = {'windows_timezone_id': before.get('timezone_id'),
                          'current_os_utc_offset_minutes': before.get('utc_offset_minutes'),
                          'service_process_timezone_verified': False,
                          'device_clock_and_server_conversion_verified': False}
    report['environment_override_presence'] = before.get('override_flags')
    observations = {}
    # Config bytes are never hashed into the report or printed: they contain secrets.
    config, config_mtime = read_source(root, 'local_config.py')
    policy = None if settings_only else config_policy(config)
    if settings_only:
        report['sanitized_settings'] = inspect_config(config)
    required = ['erpnext_sync.py', 'erpnext_sync_win.py', 'SMWinservice.py']
    if policy is not None and policy['candidate_sending_path'] == 'durable':
        required += ['durable_sync.py', 'durable_delivery.py', 'punch_normalization.py']
    for name in required:
        data, modified = read_source(root, name)
        observations[name] = (data, modified)
        report['sources'][name] = {'sha256': digest(data), 'reviewed_source_match':
                                  digest(data.replace(b'\r\n', b'\n')) == REVIEWED[name]}
    source_match = all(x['reviewed_source_match'] for x in report['sources'].values())
    if settings_only:
        normalized = {name: digest(data.replace(b'\r\n', b'\n')) for name, (data, _) in observations.items()}
        profile = ('478be326ff35fbc8a62a150866eb976b6a658544' if source_match else
                   'a23b8112fd1b87f3d2bb10f798b45e150aadcd7a' if normalized == PRODUCTION_REVIEWED else None)
        report['matched_configuration_loader_profile'] = profile
        # Keep the original reference-match flags and raw file hashes unchanged.
        # A new profile is a separate binding, not an overwrite of old evidence.
        if profile is None:
            report['findings'].append('CONFIGURATION_LOADER_SOURCE_UNREVIEWED')
    elif source_match:
        report['on_disk_candidate_policy'] = policy
        report['timestamp_transformations_in_reviewed_source'] = {
            'payload': 'str(naive local datetime); no explicit UTC conversion or microsecond truncation',
            'retry_dump': 'legacy JSON epoch serialization uses datetime.timestamp; read uses host-local fromtimestamp',
            'selected_normalizer': ('rejects aware datetimes; numeric timestamps use host-local fromtimestamp'
                                    if policy['candidate_sending_path'] == 'durable' else 'legacy path; durable normalizer not selected'),
            'direction': 'AUTO: OUT codes first, then IN codes, otherwise None',
            'scope': 'reviewed on-disk source only; no claim about service memory, devices or Frappe'}
    else:
        report['findings'].append('SOURCE_DIFFERS_FROM_REVIEWED_PROFILE_POLICY_WITHHELD')
    started = before.get('started_utc')
    if started:
        started_epoch = dt.datetime.fromisoformat(started.replace('Z', '+00:00')).timestamp()
        report['candidate_files_changed_since_process_start'] = any(
            t > started_epoch for t in [config_mtime] + [x[1] for x in observations.values()])
    else:
        report['candidate_files_changed_since_process_start'] = None
    report['alternate_local_import_artifacts_present'] = any((root / name).exists() for name in (
        'local_config.pyc', 'local_config/__init__.py', 'erpnext_sync.pyc', 'erpnext_sync/__init__.py',
        'sitecustomize.py', 'usercustomize.py'))
    # This does not read the service heap, sys.path, .pth/sitecustomize code, or
    # existing bytecode. These remain explicit limits even when all checks pass.
    after = inspect_service()
    require(before == after, 'SERVICE_OR_HOST_OBSERVATION_CHANGED_RETRY_LATER')
    require(checkout_revision(root) == revision_before, 'CHECKOUT_REVISION_CHANGED_DURING_READ')
    require(read_source(root, 'local_config.py') == (config, config_mtime), 'CONFIG_CHANGED_DURING_READ')
    for name, observation in observations.items():
        require(read_source(root, name) == observation, 'SOURCE_CHANGED_DURING_READ')
    for key in ('registration_matches_selected_root', 'process_command_matches_selected_root'):
        if not report[key]:
            report['findings'].append(key.upper() + '_UNVERIFIED')
    if report['alternate_local_import_artifacts_present']:
        report['findings'].append('ALTERNATE_IMPORT_ARTIFACT_REQUIRES_REVIEW')
    if report['candidate_files_changed_since_process_start']:
        report['findings'].append('ON_DISK_SETTINGS_MAY_DIFFER_FROM_RUNNING_MEMORY')
    report['findings'].append('LOADED_MODULES_CONFIG_AND_PROCESS_ENVIRONMENT_NOT_INSPECTED')
    report['status'] = 'STATIC_CANDIDATE_ONLY_NOT_RUNTIME_ATTESTATION'
    if settings_only:
        # Configuration output is exactly the requested allowlist. Existing audit
        # checks survive as confidence metadata, not additional configuration.
        return {
            'schema': 'windows-integration-sanitized-settings-v1',
            'settings': report['sanitized_settings'],
            'confidence': {
                'scope': 'on_disk_declarations_only_no_defaults_inferred',
                'loaded_runtime_configuration_verified': False,
                'source_traceability': {key: report[key] for key in (
                    'observed_at_utc', 'reviewed_source_revision', 'deployed_checkout_revision',
                    'sources', 'matched_configuration_loader_profile')},
                'checks': {key: report[key] for key in (
                    'registration_matches_selected_root', 'process_command_matches_selected_root',
                    'candidate_files_changed_since_process_start', 'alternate_local_import_artifacts_present',
                    'environment_override_presence', 'findings')},
            },
        }
    return report


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise DiagnosticError('INVALID_ARGUMENTS_USE_HELP')


def main(argv=None):
    try:
        parser = SafeParser(description=__doc__, allow_abbrev=False)
        parser.add_argument('--integration-root', required=True,
                            help='Administrator-identified service source directory')
        parser.add_argument('--settings-only', action='store_true',
                            help='Requested sanitized declarations plus confidence/source audit metadata only')
        args = parser.parse_args(argv)
        require(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode,
                'RUN_WITH_ISOLATED_NO_SITE_NO_BYTECODE_FLAGS')
        report = diagnose(args.integration_root, settings_only=args.settings_only)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 0  # Completed observation, NOT verification/certification success.
    except DiagnosticError as exc:
        print(json.dumps({'status': 'DIAGNOSTIC_BLOCKED', 'reason': str(exc)}))
        return 2
    except Exception:
        # Never print tracebacks, exception text, paths, config lines, or stderr.
        print(json.dumps({'status': 'DIAGNOSTIC_BLOCKED', 'details_suppressed': True}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
