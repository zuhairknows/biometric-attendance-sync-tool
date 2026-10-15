"""Validate administrator-reviewed deployment evidence without importing Frappe/config."""
import ast
import datetime as dt
import hashlib
import json
from pathlib import Path

from scripts.attendance_reconciliation import DEVICES, child_file, require, verified_json, unique_object

AUTHORITATIVE_URL = 'https://erp.myfpf.com'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def read_json(path):
    # A parent review pins this file's bytes; this helper still rejects duplicate JSON keys.
    path = Path(path)
    require(path.stat().st_size <= 1024 * 1024, 'review_profile_too_large')
    return verified_json(path, hashlib.sha256(path.read_bytes()).hexdigest())[0]


def literal_policy(path):
    """Read only allowlisted literal settings. Never execute config or evaluate credentials."""
    path = Path(path)
    require(path.stat().st_size <= 1024 * 1024, 'integration_config_too_large')
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    names = {'ERPNEXT_URL', 'ERPNEXT_VERSION', 'devices', 'device_punch_values_IN', 'device_punch_values_OUT'}
    values, assignments = {}, {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    require(len(node.targets) == 1, 'aliased_policy_assignment_unsupported')
                    require(target.id not in assignments, 'dynamic_policy_unsupported')
                    assignments[target.id] = node
                    values[target.id] = node.value
    for name in names:
        for node in tree.body:
            if node is not assignments.get(name):
                require(not any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node)), 'dynamic_policy_unsupported')
    url = ast.literal_eval(values['ERPNEXT_URL'])
    require(isinstance(url, str) and url.rstrip('/') == AUTHORITATIVE_URL, 'integration_site_not_authoritative')
    version = ast.literal_eval(values['ERPNEXT_VERSION']) if 'ERPNEXT_VERSION' in values else 14
    require(type(version) is int and version >= 12, 'integration_version_unsupported')
    in_codes = ast.literal_eval(values['device_punch_values_IN']) if 'device_punch_values_IN' in values else [0, 4]
    out_codes = ast.literal_eval(values['device_punch_values_OUT']) if 'device_punch_values_OUT' in values else [1, 5]
    for codes in (in_codes, out_codes):
        require(isinstance(codes, (list, tuple)) and all(type(c) is int and 0 <= c <= 255 for c in codes), 'policy_codes_invalid')
    devices = values['devices']
    require(isinstance(devices, (ast.List, ast.Tuple)), 'literal_devices_required')
    policies, seen = {}, set()
    for device in devices.elts:
        require(isinstance(device, ast.Dict), 'literal_devices_required')
        fields = {}
        for key, value in zip(device.keys, device.values):
            require(isinstance(key, ast.Constant) and isinstance(key.value, str), 'literal_devices_required')
            if key.value in ('device_id', 'punch_direction'):
                require(key.value not in fields, 'duplicate_policy_field')
                fields[key.value] = value
        identifier = ast.literal_eval(fields['device_id'])
        require(isinstance(identifier, str) and identifier not in seen, 'duplicate_or_invalid_device_id')
        seen.add(identifier)
        if identifier in DEVICES:
            direction = ast.literal_eval(fields['punch_direction']) if 'punch_direction' in fields else None
            require(direction in ('AUTO', 'IN', 'OUT', None), 'policy_direction_invalid')
            policies[identifier] = dict(punch_direction=direction, in_codes=list(in_codes), out_codes=list(out_codes))
    require(set(policies) == set(DEVICES), 'selected_device_policies_missing')
    app = 'hrms' if version > 13 else 'erpnext'
    return dict(authoritative_url=AUTHORITATIVE_URL, device_policies=policies,
                endpoint_path=app + '.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field')


def syntax_equal(left, right):
    return ast.dump(left, include_attributes=False) == ast.dump(ast.parse(right, mode='eval').body, include_attributes=False)


def infer_semantics(source):
    """Recognize a deliberately narrow endpoint/validator shape; reject unknown code."""
    tree = ast.parse(source)
    endpoints = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'add_log_based_on_employee_field']
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'EmployeeCheckin']
    require(len(endpoints) == len(classes) == 1, 'endpoint_or_controller_unsupported')
    endpoint = endpoints[0]
    defaults = dict(zip([a.arg for a in endpoint.args.args][-len(endpoint.args.defaults):], endpoint.args.defaults))
    require(isinstance(defaults.get('employee_fieldname'), ast.Constant)
            and defaults['employee_fieldname'].value == 'attendance_device_id', 'lookup_field_unsupported')
    lookups = [n for n in ast.walk(endpoint) if isinstance(n, ast.Call) and syntax_equal(n.func, 'frappe.db.get_values')]
    require(len(lookups) == 1, 'lookup_implementation_unsupported')
    lookup = lookups[0]
    require(len(lookup.args) == 3 and syntax_equal(lookup.args[0], "'Employee'")
            and syntax_equal(lookup.args[1], '{employee_fieldname: employee_field_value}')
            and syntax_equal(lookup.args[2], "['name', 'employee_name', employee_fieldname]")
            and len(lookup.keywords) == 1 and lookup.keywords[0].arg == 'as_dict'
            and syntax_equal(lookup.keywords[0].value, 'True'), 'lookup_implementation_unsupported')
    methods = {n.name: n for n in classes[0].body if isinstance(n, ast.FunctionDef)}
    duplicate = methods.get('validate_duplicate_log')
    require(duplicate is not None and not duplicate.decorator_list and len(duplicate.body) == 2
            and isinstance(duplicate.body[0], ast.Assign) and len(duplicate.body[0].targets) == 1
            and isinstance(duplicate.body[0].targets[0], ast.Name), 'duplicate_validator_unsupported')
    call = duplicate.body[0].value
    require(isinstance(call, ast.Call) and syntax_equal(call.func, 'frappe.db.exists') and len(call.args) == 2
            and not call.keywords and syntax_equal(call.args[0], "'Employee Checkin'")
            and isinstance(call.args[1], ast.Dict), 'duplicate_validator_unsupported')
    filters = call.args[1]
    fields = {}
    for key, value in zip(filters.keys, filters.values):
        require(isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value not in fields, 'duplicate_validator_unsupported')
        fields[key.value] = value
    require(set(fields) in ({'employee', 'time', 'name'}, {'employee', 'time', 'name', 'log_type'}), 'duplicate_key_unsupported')
    for field in ('employee', 'time', 'log_type'):
        if field in fields:
            require(syntax_equal(fields[field], 'self.' + field), 'duplicate_key_unsupported')
    require(syntax_equal(fields['name'], "('!=', self.name)"), 'duplicate_key_unsupported')
    branch = duplicate.body[1]
    require(isinstance(branch, ast.If) and isinstance(branch.test, ast.Name)
            and branch.test.id == duplicate.body[0].targets[0].id and not branch.orelse, 'duplicate_validator_unsupported')
    throws = [n for n in ast.walk(branch) if isinstance(n, ast.Call) and syntax_equal(n.func, 'frappe.throw')]
    require(len(throws) == 1 and isinstance(branch.body[-1], ast.Expr)
            and isinstance(branch.body[-1].value, ast.Call) and syntax_equal(branch.body[-1].value.func, 'frappe.throw')
            and all(isinstance(n, (ast.Assign, ast.Expr)) for n in branch.body), 'duplicate_validator_unsupported')
    validate = methods.get('validate')
    require(validate is not None and not validate.decorator_list
            and all(isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) for n in validate.body) and any(
        isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and syntax_equal(n.value, 'self.validate_duplicate_log()')
        for n in validate.body), 'duplicate_validation_not_unconditional')
    before = methods.get('before_validate')
    precision = 'microseconds'
    if before is not None:
        expected = ast.parse('self.time = get_datetime(self.time).replace(microsecond=0)').body[0]
        require(not before.decorator_list and len(before.body) == 1
                and ast.dump(before.body[0], include_attributes=False) == ast.dump(expected, include_attributes=False), 'timestamp_hook_unsupported')
        precision = 'seconds'
    return dict(employee_field='attendance_device_id', duplicate_key='employee_time_log_type' if 'log_type' in fields else 'employee_time',
                timestamp_precision=precision)


def load_review(path, digest, policy):
    review, sha, resolved = verified_json(path, digest)
    require(review.get('schema') == 'frappe-export-review-v1' and review.get('authoritative_url') == AUTHORITATIVE_URL, 'authoritative_site_review_required')
    for field in ('route_to_database_verified', 'deployment_sources_verified', 'deployment_frozen_for_export',
                  'production_policy_verified', 'lookup_sql_equivalence_reviewed', 'all_validation_paths_reviewed', 'no_identity_mutating_hooks'):
        require(review.get(field) is True, 'administrator_review_incomplete')
    require(isinstance(review.get('evidence_reference'), str) and bool(review['evidence_reference'].strip()), 'review_evidence_reference_required')
    require(dt.datetime.fromisoformat(review['reviewed_at']).tzinfo is not None
            and dt.datetime.fromisoformat(review['expires_at']).tzinfo is not None
            and dt.datetime.fromisoformat(review['reviewed_at']) < dt.datetime.fromisoformat(review['expires_at']), 'review_dates_invalid')
    now = dt.datetime.now(dt.timezone.utc)
    require(dt.datetime.fromisoformat(review['reviewed_at']) <= now <= dt.datetime.fromisoformat(review['expires_at']), 'review_expired_or_future')
    require(review.get('integration_policy_sha256') == fingerprint(policy), 'production_policy_review_mismatch')
    require(review.get('endpoint_path') == policy['endpoint_path'], 'integration_endpoint_review_mismatch')
    require(isinstance(review.get('database_identity_sha256'), str) and len(review['database_identity_sha256']) == 64, 'database_identity_required')
    files = review['files']
    require(set(files) == {'employee_checkin', 'effective_hooks', 'frappe_database', 'frappe_query', 'integration_engine'}, 'review_source_set_incomplete')
    contents, paths = {}, [resolved]
    for role, spec in files.items():
        source_path = child_file(resolved.parent, spec['file'])
        require(source_path.stat().st_size <= 4 * 1024 * 1024, 'review_source_too_large')
        data = source_path.read_bytes()
        require(len(data) <= 4 * 1024 * 1024 and hashlib.sha256(data).hexdigest() == spec['sha256'], 'deployed_source_hash_mismatch')
        contents[role] = data.decode('utf-8-sig')
        paths.append(source_path)
    semantics = infer_semantics(contents['employee_checkin'])
    require(semantics == review.get('semantics'), 'reviewed_semantics_disagree_with_source')
    hooks = json.loads(contents['effective_hooks'], object_pairs_hook=unique_object)
    require(hooks.get('schema') == 'frappe-effective-hooks-v1' and isinstance(hooks.get('installed_apps'), list)
            and hooks['installed_apps'] and all(isinstance(a, str) for a in hooks['installed_apps'])
            and len(set(hooks['installed_apps'])) == len(hooks['installed_apps']), 'effective_hooks_inventory_invalid')
    # Unsupported overrides/events block certification, including wildcard document hooks.
    for field in ('override_whitelisted_methods', 'override_doctype_class', 'extend_doctype_class', 'doc_events'):
        require(isinstance(hooks.get(field), dict), 'effective_hooks_inventory_incomplete')
    require(not hooks['override_whitelisted_methods'].get(review.get('endpoint_path'))
            and not hooks['override_doctype_class'].get('Employee Checkin')
            and not hooks['extend_doctype_class'].get('Employee Checkin')
            and not hooks['doc_events'].get('Employee Checkin') and not hooks['doc_events'].get('*'), 'custom_identity_hooks_unsupported')
    require(review.get('endpoint_path') in ('hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field',
                                         'erpnext.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field'), 'endpoint_path_unsupported')
    # These sources are reviewed as a whole, not executed. Their hashes bind the SQL-equivalence
    # and integration-call review; the operator must verify deployment provenance externally.
    for role in ('frappe_database', 'frappe_query', 'integration_engine'):
        ast.parse(contents[role])
    return review, semantics, hooks, sha, paths
