# Integration-account GET-only verification

> Historical procedure: core Phase 1 deployment and integration-account GET checks have completed. Do not rerun the commands below as a current next step. See [current Phase 1 status](phase1-status.md); Auto Attendance remains blocked pending a separately verified safeguard.

Preparation only. No live request was made during development. Do not call the
insertion endpoint, import integration modules, or change the service/configuration.
Use `scripts/verify_integration_get_only.py` alone, outside the deployed repository.
It needs only trusted standard-library Python; no package installation.

## Required administrator controls

An administrator must privately prepare `phase1-get-controls.json` using an
independent authoritative read, not this same integration account or the MCP
connector's potentially filtered results. Do not put it in Git or a public folder.
Use existing records only. The expected user must be independently bound to the
configured API key by the administrator; never include that key in the control file.

For each exact filter below, provide the **complete** authoritative matching rows
and count, across all devices, including unrelated/blank device IDs. Select small
sets of zero, one or two rows. Larger sets are deliberately unsupported because
the engine uses limit 2 without ordering. Include a unique active employee and an
existing Checkin whose stored log type is an empty string. Preserve SQL NULL as JSON
null; do not normalize it. Include inactive/duplicate employee controls where they
exist, and controls spanning applicable user-permission scopes. Do not create any
control records. If no suitable existing blank record exists, verification blocks.

The administrator must review effective roles, field permissions, User Permissions,
permission-query customizations and both resource routes for the **actual API user**.
Only then may they attest `permission_scope_reviewed`. Control agreement alone
cannot prove absence of filtering outside the sampled controls. App/runtime-hook
compatibility remains a separate gate. `all_device_scope_reviewed` means the
independent lookup applied no device restriction, not that this helper fetched
device fields. The helper never sets these attestations itself or verifies their
authorship cryptographically; the operator must obtain the approved file privately.

Schema illustration below uses synthetic identities and deliberately unapproved
flags. Replace with authoritative existing records privately. Approval must include
a timezone and be no more than 24 hours old when run. If relevant rows/settings
change after preparation, refresh controls rather than accepting a mismatch.

```json
{
  "schema": "phase1-get-controls-v1",
  "expected_user": "SYNTHETIC-INTEGRATION-USER",
  "administrator_reference": "REPLACE-WITH-APPROVED-REVIEW-REFERENCE",
  "approved_at": "REPLACE-WITH-CURRENT-ISO-TIMESTAMP-WITH-OFFSET",
  "authoritative_full_sets": false,
  "permission_scope_reviewed": false,
  "all_device_scope_reviewed": false,
  "employees": [{
    "employee_field_value": "0001",
    "authoritative_count": 1,
    "expected_rows": [{
      "name": "SYNTHETIC-EMPLOYEE",
      "status": "Active",
      "attendance_device_id": "0001"
    }]
  }],
  "checkins": [{
    "employee": "SYNTHETIC-EMPLOYEE",
    "timestamp": "2026-10-14 08:00:00.123456",
    "authoritative_count": 1,
    "expected_rows": [{
      "name": "SYNTHETIC-CHECKIN",
      "employee": "SYNTHETIC-EMPLOYEE",
      "time": "2026-10-14 08:00:00",
      "log_type": ""
    }]
  }]
}
```

## One execution command, after approval

Privately transfer only the reviewed helper and approved controls to the production
operator's Downloads folder. Do not transfer credentials or change the checkout.
Use the same verified checkout path from the earlier service diagnostic:

```powershell
py -I -S -B "$env:USERPROFILE\Downloads\verify_integration_get_only.py" "C:\GitHub\biometric-attendance-sync-tool\local_config.py" "$env:USERPROFILE\Downloads\phase1-get-controls.json"
```

No administrator elevation or service operation is required; the operator must
already have read access to these private files. Do not grant permissions merely
to make the helper pass. The script performs the GETs only when this command is run.

## What it checks and what PASS means

- Parses only inert literal configuration via AST. Rejects duplicates, executable
  statements/imports and unsupported definitions without executing them. Requires
  literal key/secret and the exact approved URL `https://erp.myfpf.com` (optional
  trailing slash). It never hashes, copies or prints credential/configuration values.
- Uses direct HTTPS with certificate/hostname verification to that fixed host only;
  ignores environment proxies/netrc, disallows redirects and sends GET only. Three
  routes exist: authenticated identity and the two resource lists. No insertion
  route or write operation exists. No retry on failures. Responses are bounded to
  1 MiB; at most 20 cases per resource. Credentials remain in memory only.
- Identity GET: `/api/method/frappe.auth.get_logged_user`; compares with the approved
  account without displaying it. A mismatch blocks all resource requests.
- Employee GET fields: `name,status,attendance_device_id`; exact filter
  `attendance_device_id = employee_field_value`, with leading zeros preserved.
- Checkin GET fields: `name,employee,time,log_type`; exact filters `employee = ...`,
  `time = naive timestamp truncated to seconds`, `log_type = ""`; limit 2 for both
  resource routes, with no device filter. These match `FrappeClient.confirm` in
  `durable_delivery.py` at the pinned engine revision `4256cd6`.
- Compares complete control sets/counts, preserving NULL versus empty string and
  returned timestamp precision. Truncation applies to the query input only; no
  source or attendance value is modified. Detects input file changes during the run
  through in-memory byte comparison, without writing backups or credential hashes.

PASS: exit 0 and four PASS lines for identity, Employee reads, blank-log Checkin reads
and administrator-control-set completeness. This means those independently approved
control sets agree; it is not an atomic full export or a global runtime attestation.

FAIL/UNVERIFIED: exit 1 with only `GET_VERIFICATION: FAIL_OR_UNVERIFIED`. Includes
missing/unapproved controls, configuration uncertainty, identity mismatch, TLS/HTTP
errors, redirects, missing/extra/filtered rows, altered fields, NULL/blank or precision
differences. Raw responses, usernames, identifiers, headers, exceptions and paths are
never printed. Do not enable debug logging or dump local variables to investigate.

The helper reads the current on-disk credentials, not service memory. The accepted
process/root/unchanged-file evidence binds the configuration candidate to the
running integration but cannot prove retained in-memory values. Output explicitly
leaves `LOADED_SERVICE_CREDENTIALS` and `EFFECTIVE_SERVER_HOOKS` UNVERIFIED. Do not
restart or inspect service memory to close that gap within this procedure. No result
authorizes deployment, attendance submission or recovery, or sets verification flags.
