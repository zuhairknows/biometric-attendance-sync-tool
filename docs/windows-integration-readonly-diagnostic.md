# Windows integration diagnostic — review before production execution

Service-hardening finalization note: tests now load the immutable `478be326` source blobs
instead of copying the working tree. All existing development and production profile
hashes are unchanged and verified against their respective Git revisions. The new hardened
source deliberately remains unrecognized by this diagnostic; passing tests do not approve
a new runtime profile. Git preserves this standalone script's exact bytes so the approved
transfer hash below survives checkout. No production operation is performed by this change.

This is a separate diagnostic, not reconciliation, export, deployment or recovery
tooling. Production execution is **pending operator review/authorization**. Nothing
in this change runs the script on the production PC, transfers files there, changes
the service, or reruns the accepted provisional reconciliation.

## Files to review

- Standalone script: `scripts/diagnose_windows_integration.py` (standard library only).
- Synthetic tests: `tests/test_windows_integration_diagnostic.py`.
- This runbook. No other script/module from this repository needs to be installed
  or imported to run the diagnostic.

## Narrow configuration inspection (updated review)

Use **`--settings-only`** for the current task. The `settings` object contains
exactly these six entries, with no other configuration values:

- `FP2_DEVICE_08.punch_direction`
- `FP2_DEVICE_11.punch_direction`
- `device_punch_values_IN`
- `device_punch_values_OUT`
- `ERPNEXT_VERSION`
- `erpnext_url_matches_approved_site` (boolean value only; URL never printed)

Each entry has a `state`, `present`, and `confidence`. Values appear only for valid
literal declarations. `None` direction is JSON `null` with `state=literal` and
`present=true`; an absent field has `state=absent`, `present=false`, and no `value`.
No source default, last assignment, alias, expression or branch is evaluated to
fill an absent/uncertain value. Missing device entries are distinguished from
missing direction fields by a fixed reason code.

States are `literal`, `absent`, `dynamic`, `ambiguous` or `invalid_literal`.
Unknown/invalid values are withheld, never echoed. Direction output permits only
None/AUTO/IN/OUT; code lists/tuples permit only integers 0-255; version permits only
integer 12-99. Other literal types/ranges are reported as invalid, not corrected.
URL comparison is an exact match against the approved two HTTPS site aliases,
with an optional trailing slash. Other literal strings return only false,
including URLs containing credentials, paths or query strings. Tuple code lists
are represented as JSON arrays, retaining order and duplicates.

The AST inspection never evaluates the device dictionaries or unrelated settings.
Bare names remain unresolved. Duplicate assignments/device IDs/direction fields
and uncertain device keys are ambiguous. Any module-level executable construct,
import, mutation, conditional, definition or potentially side-effecting expression
makes the requested settings unresolved: even apparently unrelated code could
change globals. This conservative rule also applies to dynamic credential loading.
No source snippets, unresolved names/expressions, arbitrary strings or config hash
are printed. Syntax/encoding failures produce only the existing safe blocked result.

The existing service/source/file-stability checks still run. Their non-secret audit
results are retained under `confidence`, including source hashes and checkout
metadata; they are not extra configuration settings. PID, raw command lines,
timezone details, inferred sending path and endpoint are omitted in settings-only
output. No configuration value is certified as effective in-memory behavior.

This mode separately recognizes the already reviewed production loader core at
`a23b8112fd1b87f3d2bb10f798b45e150aadcd7a`, as well as the development loader core.
The old development reference hashes remain unchanged. The production loader
profile is matched as a complete set of engine/launcher/service-base hashes, not
an arbitrary mixture. It does not attest SDKs, server code, a durable path, dirty
files beyond the observed set, or loaded modules. Unknown source preserves the
AST declaration observation and an explicit unreviewed-loader confidence finding;
it never enables the older mode's policy inference or a certification flag.

The existing broader diagnostic mode remains available without `--settings-only`;
its strict configuration and source validation still apply. It now reports only
the approved-site boolean in place of a raw configured URL. For this task, use
the settings-only commands below.

## What the repository establishes

At repository commit `478be326ff35fbc8a62a150866eb976b6a658544`:

1. `erpnext_sync_win.py` imports `main` from `erpnext_sync` once at service startup.
   The service loop calls it until stopped. The reviewed launcher registers a direct
   `python.exe erpnext_sync_win.py` service command.
2. `erpnext_sync.py` imports `local_config as config` at module import. It reads the
   IN/OUT code lists and ERPNext version at module import, with defaults IN `[0,4]`,
   OUT `[1,5]`, version `14`. These are source defaults, not verified production values.
3. `main` reads `DURABLE_SYNC_ENABLED` (default `False`) each cycle. `False` selects
   legacy sending; `True` selects `durable_sync.run`. A non-boolean stops processing.
   The configuration module itself is not automatically reloaded from disk each cycle.
4. Both reviewed sending paths select `hrms` for version >13, otherwise `erpnext`:
   `<app>.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field`.
   They send terminal `user_id` as `employee_field_value`; no employee-field override
   is supplied by the reviewed implementation.
5. Normalization defaults an absent per-device direction to `None`. `AUTO` tests
   configured OUT codes first, then IN codes, otherwise `None`; explicit IN/OUT
   passes through. A configured `None` is not evidence of how the server treats it.
6. Naive datetime strings are sent without explicit UTC conversion or microsecond
   truncation. Legacy retry JSON uses epoch serialization and host-local
   `datetime.fromtimestamp` when reading; this can depend on host timezone/DST.
   The durable normalizer rejects aware datetimes and parses naive ISO timestamps;
   numeric timestamps also use host-local `fromtimestamp`.

Importing the integration for inspection is unsafe: it initializes logging/status
and can open runtime paths. The diagnostic **never imports or executes** the
integration, `local_config`, SDKs, retry loaders, or ledger code.

## Observation method and safety boundaries

The script makes two fixed **local, read-only** PowerShell observations. It queries
`Win32_Service` for the exact service name `ERPNextBiometricPushService`, its process
command/start time, the OS timezone/current offset, and presence flags for
`PYTHONPATH`, `PYTHONHOME` and `TZ` in machine/service/diagnostic-process environments.
It does not inspect another computer, query Frappe, or communicate with terminals.
Command comparisons occur inside PowerShell: only boolean flags cross its stdout
pipe, never raw SCM/process command lines. No secret is interpolated into child
process arguments. Only the non-secret selected root is added to its environment.
No method that starts/stops/reconfigures the service is present. There is no memory
attachment, code injection, process dump, registration write or deliberate write test.

It reads only named source/config files in the administrator-selected source root.
It also reads bounded local `.git/HEAD` and referenced loose/packed ref metadata as
data to record the selected production checkout commit, without executing Git or
hooks. External/linked-worktree metadata, missing Git data or unsupported refs yield
an explicit unavailable status; no revision is fabricated. Branch/ref text is not
printed. Git metadata is checked again for changes at the end of observation.
The original broader mode parses `local_config.py` with Python AST as data, without evaluating executable
expressions. Dynamic content, imports, reassignment, mutation, unsupported structure,
missing/duplicate device IDs, invalid policy types and unapproved destination URLs
block output with a fixed diagnostic code. It never evaluates configuration secrets.
This deliberately rejects some valid but dynamic configs rather than inventing values.

Source hashes must match the reviewed profile (only CRLF/LF differences are ignored)
before reporting an on-disk candidate path, endpoint or timestamp transformations.
Legacy requires matching engine/launcher/service-base files. Durable additionally
requires matching delivery, orchestration and normalization files. Unknown/older
code produces source hashes plus a source-mismatch finding and **withholds policy**.
Do not edit production files or update trusted hashes just to make that check pass:
review the differing non-secret source first and prepare a separately reviewed profile.
`reviewed_source_revision` identifies this diagnostic's reference profile;
`deployed_checkout_revision.commit` identifies the selected on-disk checkout when
available. They must not be confused. Per-file raw SHA-256 hashes record the actual
observed files. A checkout commit is not proof of clean files, loaded service code,
or incident-time source: both files-match-commit and loaded-revision verification
remain false. No Git pull, checkout, branch switch, install or source edit is needed.

The service registration and actual process command must independently match the
selected source root. Only the reviewed two-argument absolute Python launcher is
recognized. Alternate wrappers, extra flags, inaccessible command lines or another
checkout produce explicit unverified findings. The script never executes the
registered interpreter or service command. A stopped service has no verified process.

Files and service observations are checked again at completion. An observed change
blocks the result. File modification times after process startup flag possible stale
service configuration; older modification times alone do not prove loaded contents.
Alternate source/package/legacy-bytecode artifacts and local startup customization
files are flagged without executing them. Relevant environment overrides are reported
as presence booleans; their raw values and full service command lines are suppressed.

The diagnostic prints JSON to stdout only. It does not create a log or output file,
install packages, write bytecode, modify source/config, open retry dumps or SQLite,
read attendance records, or prepare an import. It prints no API keys, secrets,
terminal passwords/addresses, employee identifiers, full config, raw command line,
raw exceptions, tracebacks or PowerShell stderr. Config hashes are not published.
The target URL is never emitted; only its approved-site comparison boolean is eligible.

## What a successful observation cannot establish

Every completed original-mode result is `STATIC_CANDIDATE_ONLY_NOT_RUNTIME_ATTESTATION`, and
`loaded_runtime_configuration_verified` is always false. The useful output is
`on_disk_candidate_policy` in the original mode, or `settings` declarations in the
new mode, never a claim that those are the service's in-memory values.

Without attaching to or instrumenting the running service, this diagnostic cannot
prove its loaded module locations, retained global values, inherited process
environment, sys.path, cached bytecode, site-packages startup hooks or monkey patches.
It does not scan .pth files or the service account's environment hive. Presence checks
are not an exhaustive override inventory. The diagnostic's isolated Python environment
is deliberately different from the service environment. The reported timezone is
the current OS setting, **not verified service-process, device-clock, server parsing,
incident-time or historical timezone behavior**.

Administrator review must bind the installed command, actual loaded source/config,
service-start/config-change history and absence of relevant runtime overrides before
using on-disk values as effective production evidence. If that cannot be established
without an intrusive action, leave it unresolved. Do not restart or instrument the
service merely to resolve this diagnostic. This tool cannot certify server duplicate
identity, whether log_type participates, or server timestamp/hook behavior.

## Exact proposed execution — after separate review/authorization

1. Review the standalone script and these instructions on the development PC. Record
   its approved SHA-256 out of band. Review the embedded fixed PowerShell query and
   source profile. No production access is required for this review.
2. After operator approval, place only the reviewed script in an administrator-controlled
   diagnostic directory outside the integration checkout, state directories and
   existing reconciliation reports. Do not overwrite an existing file. This is a
   diagnostic copy, not a service deployment. Verify the transferred script's hash
   against the independent approved value. No helper package is required.
3. On the production PC use an approved, existing trusted Python 3.10+ interpreter
   with its standard library available. Do not install or upgrade Python/packages.
   Use a local Windows PowerShell 5.1 terminal under an account already allowed to
   read the service/process metadata, service registry key and source/config files.
   If permission is denied, stop and request administrator assistance; do not grant
   permissions, impersonate the service or change execution policy for this task.
4. Set the three paths to reviewed actual locations. The integration root below is
   the stated repository location; an administrator must confirm it matches the
   installed service, not assume that a repository path proves this.

```powershell
$diagnosticPython = 'C:\ApprovedPython\python.exe'
$diagnosticScript = 'C:\ApprovedDiagnostics\diagnose_windows_integration.py'
$integrationRoot = 'C:\GitHub\biometric-attendance-sync-tool'

# Compare this hash with the independently approved script hash before execution.
Get-FileHash -LiteralPath $diagnosticScript -Algorithm SHA256

# -I ignores Python path/environment injection; -S skips site startup code;
# -B prevents bytecode writes. Do not remove these flags or run the service launcher.
& $diagnosticPython -I -S -B $diagnosticScript --integration-root $integrationRoot --settings-only
$LASTEXITCODE
```

These commands are proposed instructions, **not executed production commands**.
The placeholder interpreter/diagnostic paths must be replaced with approved existing
locations. Do not substitute `erpnext_sync.py`, `erpnext_sync_win.py`, `sync_cli.py`,
`python -m ...`, service `debug/start/restart`, or a command copied from SCM.
`--help` only prints usage and performs no service/config observation.

Exit `0` means the read-only observation completed; it never means runtime/server
certification. Exit `2` is blocked, with fixed/suppressed diagnostics. Do not enable
tracebacks or dump configuration to diagnose failure. A source mismatch or missing
candidate policy is evidence requiring source review, not an instruction to force
legacy settings or assumed defaults. Keep JSON in an approved private incident
location if retaining it; the script itself performs no report-file writes. Avoid
transcripts that also capture unrelated commands or private configuration.

### Transfer only the reviewed standalone script

The following are complete proposed commands using an approved removable drive
mounted as `E:`. Substitute that approved staging-drive letter if different. No
production Git operation is needed. Only the script is necessary on production;
keep this runbook and tests on the development PC for review. Record the approved
script hash from the final safety review independently of the transferred copy.

On the development PC:

```powershell
$sourceScript = 'C:\GitHub\biometric-attendance-sync-tool\scripts\diagnose_windows_integration.py'
$transferDirectory = 'E:\FPF-Diagnostic-Settings-Review-20261015'
if (Test-Path -LiteralPath $transferDirectory) { throw 'Use a new staging directory; do not overwrite.' }
New-Item -ItemType Directory -Path $transferDirectory -ErrorAction Stop | Out-Null
[IO.File]::Copy($sourceScript, (Join-Path $transferDirectory 'diagnose_windows_integration.py'), $false)
Get-FileHash -LiteralPath $sourceScript -Algorithm SHA256
Get-FileHash -LiteralPath (Join-Path $transferDirectory 'diagnose_windows_integration.py') -Algorithm SHA256
```

On production, after approval, use an existing trusted interpreter. The `.venv`
path below is a candidate, not a verified production interpreter location; replace
it with the administrator-approved existing interpreter if necessary. If none is
available, stop rather than install anything. Do not discover it by executing the
service's full command line.

```powershell
$ErrorActionPreference = 'Stop'
$approvedHash = 'A5092469AFBEDE36D5649BF0F92A5505072D1DC8759DCF62932FB909624FB0CA'
$transferScript = 'E:\FPF-Diagnostic-Settings-Review-20261015\diagnose_windows_integration.py'
$integrationRoot = 'C:\GitHub\biometric-attendance-sync-tool'
$diagnosticPython = Join-Path $integrationRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $diagnosticPython -PathType Leaf)) { throw 'Approved existing Python required; do not install.' }
if ((Get-FileHash -LiteralPath $transferScript -Algorithm SHA256).Hash -ne $approvedHash) { throw 'Script hash mismatch; stop.' }

# Create a new private diagnostics directory, outside the deployed repository.
$runDirectory = Join-Path $env:ProgramData ('FPF-Diagnostic-Settings-20261015-' + [guid]::NewGuid().ToString('N'))
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$sids = @([System.Security.Principal.WindowsIdentity]::GetCurrent().User,
          (New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')),
          (New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')))
foreach ($sid in $sids) {
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
[IO.Directory]::CreateDirectory($runDirectory, $acl) | Out-Null
$diagnosticScript = Join-Path $runDirectory 'diagnose_windows_integration.py'
[IO.File]::Copy($transferScript, $diagnosticScript, $false)
if ((Get-FileHash -LiteralPath $diagnosticScript -Algorithm SHA256).Hash -ne $approvedHash) { throw 'Copied script hash mismatch; stop.' }

& $diagnosticPython -I -S -B $diagnosticScript --integration-root $integrationRoot --settings-only
$diagnosticExitCode = $LASTEXITCODE
Write-Output ('Diagnostic exit code: ' + $diagnosticExitCode)
```

Only a new protected diagnostic directory and script copy are created by these
transfer instructions. The diagnostic itself writes stdout only. No existing
production repository, configuration, retry/log/ledger file or service setting is
modified. Do not redirect output into production logs or existing evidence files.
The approved script hash changes if its bytes change; repeat review/hash approval
for any replacement rather than accepting a hash shipped alongside untrusted code.
The previous reviewed script SHA-256 was
`05B7D055BDDF20743C0F162884AB6DCA4036FC61A4A5B78DD487AA650190B4EC`.
Retain that historical identifier with the earlier diagnostic; do not overwrite
its output or reuse its approved hash for this updated script.

## Final safety review — October 15, 2026

1. Integration modules are read as bytes/AST only, never imported or executed.
2. No Frappe/device/external-service connection is implemented. CIM is local only.
3. The diagnostic contains no file-writing or production-document mutation path.
   Transfer commands create only the separate private diagnostic directory/copy.
4. No service control/reconfiguration operation is implemented.
5. Credentials/config bodies are not emitted or persisted. PowerShell emits command
   comparison flags only; exceptions/stderr are fixed/suppressed. No secrets are
   included in child process arguments or diagnostic debug output. OS/admin memory
   capture and unrelated endpoint monitoring are outside this utility's guarantees.
6. Only the standalone script is needed. No Git execution, checkout/pull, package
   installation or deployed-code edit is required. Existing Python 3.10+, PowerShell
   5.1 and sufficient read access are prerequisites, not production-verified facts.
   Unsupported/different production code may withhold policy or block diagnosis.
7. Every result explicitly denies proof of loaded in-memory config/server behavior.
8. Checkout commit metadata and observed source hashes are recorded when available;
   absent/unsupported Git metadata stays unavailable. Neither HEAD nor reference
   profile is misrepresented as proof of the loaded service revision or clean files.

Review result: suitable for the proposed read-only observation under these stated
prerequisites and limitations. No production execution or full runtime certification
has occurred. Existing reconciliation reports/evidence remain unchanged.

## Review output

Check root/registration/process agreement, source match flags, before/after stability,
post-start modifications and override flags before considering the candidate policy.
For each affected device, retain direction plus the shared IN/OUT lists; retain which
settings were defaulted, selected sending path, method, timestamp findings and OS zone.
Keep `loaded_runtime_configuration_verified=false` until separate administrator
evidence resolves loaded configuration. Preserve unresolved distinctions rather than
feeding this JSON directly into a reconciliation verification manifest.

An output intentionally contains no credentials or config/source bodies. It is still
operational evidence; retain it privately, separately from the accepted baseline.

## Development verification

Development verification includes **40 diagnostic tests**, including the existing safety
and settings tests, immutable Git-blob hash checks and explicit refusal to attest the
hardened worktree through an old profile. Tests require both reviewed revisions to be
available in local Git history; a shallow clone must obtain those objects before testing.
The embedded PowerShell
query was syntax-parsed without executing its commands; isolated `--help` also passed.
No real service was queried. Tests also execute only the pure launcher-parser
function with synthetic command strings, with the entire CIM/service query omitted.
An additional transport test runs the query with service, registry and timezone
cmdlets replaced by synthetic functions, proving that secret-bearing command lines
and environment values are absent from its stdout JSON; it never queries a service.
Synthetic tests cover legacy/durable routing, explicit/default IN/OUT settings, None
and AUTO, duplicate/missing devices, dynamic code and override rejection, credential
suppression, source differences, newline portability, changing files/service state,
environment/import-artifact flags, permission failure, read-only enforcement and
detached/loose/packed Git revisions, unavailable/invalid metadata and revision changes.
They use temporary synthetic configuration and mocked service observations; they do
not connect to production, devices or Frappe. Windows query syntax can be parsed
without executing its commands. Actual production permissions, CIM availability,
service registration, loaded configuration and timezone remain untested.
New tests cover exact output allowlists, literal/absent/dynamic/ambiguous settings,
invalid-secret withholding, URL suppression, module side effects, duplicate device
entries, settings-mode CLI/errors/stability, unchanged inputs and the accepted
production source profile (read from local Git blobs, never executed).

```powershell
py -B -m unittest tests.test_windows_integration_diagnostic -v
```
