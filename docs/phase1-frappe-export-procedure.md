# Phase 1 read-only Frappe attendance export

This standalone utility produces `employee-mappings.json`, `employee-checkins.csv`
and `export-manifest.json` for the existing [offline reconciliation CLI](phase1-attendance-reconciliation.md).
No exporter, site verification, or production evidence processing was run during
development. Neither `erp.myfpf.com` nor `myfpf.v.frappe.cloud` was queried.
The operator subsequently confirmed both hostnames refer to the same site. That
confirmation is accepted; approved backend/source binding is still required. For
server-local operation without external DB access, use the separate
[server-side procedure](phase1-server-frappe-export.md). The instructions below
continue to describe the original external TLS transport.

Default mode is an offline plan: no database driver import, connection, password
prompt or output writes. `--live-export --operator-authorized` enables a future
approved SELECT-only export. No Frappe application imports, API requests, framework
lifecycle, device calls, service operations, ledger/dump changes or data writes exist.

## Why a database snapshot

Separate HTTP list requests cannot establish one consistent snapshot across employee
resolution, pages and independent row counts. This implementation therefore uses a
new dedicated TLS MariaDB connection with an existing SELECT-only account and a
[READ ONLY consistent snapshot](https://mariadb.com/docs/server/reference/sql-statements/transactions/start-transaction).
It verifies REPEATABLE READ/read-only/active state, InnoDB base tables, column types,
site timezone, installed apps, reviewed server-script inventory and independent
counts. It always rolls back and closes the connection before publishing evidence.
Session transaction settings are changed only on this disposable connection.

No REST/MCP fallback exists. If Frappe Cloud cannot provide approved direct read-only
database connectivity, required metadata/grants, or reliable deployment evidence,
**stop**. Do not weaken checks, use the production write account, or substitute
permission-filtered API exports. Extending the transport needs a separate review.

## Verify the authoritative site, read-only

An administrator with hosting/deployment access must complete these checks BEFORE
approving a live export. DNS, a redirect, a matching logo/company, TLS certificate,
or similar Checkin counts do not prove two hostnames share a database.

1. On the integration PC, inspect the actual service installation and active Python
   configuration location using existing read-only administration tools. Do not
   start/stop/reconfigure it. Confirm the effective URL is `https://erp.myfpf.com`.
   Inspect the deployed `send_to_erpnext` call/config loading and actual direction
   settings. The repository template is not production evidence. Keep API/device
   secrets out of screenshots, terminal output and review artifacts.
2. Have the hosting administrator trace the HTTPS Host routing for `erp.myfpf.com`
   through the current custom-domain/proxy mapping to its exact Frappe site directory
   and database. Inspect domain mapping, site identifier and non-secret database
   target on the hosting platform. Do not copy whole `site_config.json`, nginx dumps
   or credential-bearing config into reports. Record the reviewed routing/deployment
   references privately, including time and reviewer.
3. Independently trace `myfpf.v.frappe.cloud` the same way. Only classify it as an
   alias if both routes lead to the same active site/database and deployment. If
   they differ, exclude that MCP site from this incident. The exporter always binds
   its approval to `https://erp.myfpf.com`, regardless of whether an alias exists.
4. Using an authorized existing read-only account, obtain this identity from the
   proven production database (not from an assumed MCP connection):

   ```sql
   SELECT DATABASE() AS database_name, @@hostname AS server_hostname, @@server_id AS server_id;
   ```

   Compute `fingerprint(identity_row)` using
   `scripts.frappe_export_review.fingerprint`: canonical sorted compact JSON,
   SHA-256, with `server_id` a JSON integer. Bind that digest and the route evidence
   to the review. The exporter independently checks the connected database's digest.
   This fingerprint is a guard against the wrong backend, not cryptographic proof
   of site provenance: the hosting review supplies that proof. Pin the primary
   backend; an unexpected replica/load-balancer hostname blocks instead of guessing.
5. Record the TLS hostname/CA for that database, site timezone, and reviewed
   deployment revision/hashes. Coordinate a deployment/configuration freeze for the
   short review/export window. This is not a request to stop the Windows service.
   If code, routing, hooks or policies may change during the window, do not certify.

These are future read-only review instructions, not operations performed by Codex.
The task authorizes development/publication only; live review/export requires its
own explicit approval. No hosting changes, user/grant creation or deployment is
part of this procedure. Ask the DBA for an already approved read-only account.

## Deployment behavior review and bounded support

Prepare a protected review bundle using byte-exact copies of the **deployed** files:

| Role | Evidence to inspect and hash |
| --- | --- |
| `employee_checkin` | Actual resolved endpoint/controller Python module. |
| `frappe_database` | Actual Frappe database lookup implementation. |
| `frappe_query` | Actual query/filter builder used by `get_values`. |
| `integration_engine` | Actual running integration source, including config loading, payload and punch-direction handling. |
| `effective_hooks` | JSON inventory described below, generated from the correct deployed site. |

Keep the files beside the verification JSON, with simple basenames. No source is
executed/imported by the exporter. Inspect all applicable validators, inherited
controller behavior, custom apps, monkey patches and runtime hooks. Review must
establish that none remap employee/time/log type or bypass/alter the duplicate
check. The full-source hashes and externally trusted approval bind this manual
review; AST matching alone cannot prove arbitrary Python programs safe.

The exporter recognizes a narrow standard endpoint shape: default
`employee_fieldname='attendance_device_id'`, one `frappe.db.get_values('Employee',
{employee_fieldname: employee_field_value}, ['name','employee_name',employee_fieldname],
as_dict=True)` lookup, and a straight-line duplicate validator checking employee,
time, exclusion of self.name, optionally log_type. Validation must invoke the
duplicate check unconditionally. The recognized `before_validate` is either absent
(microseconds retained) or exactly `get_datetime(self.time).replace(microsecond=0)`
(seconds truncation). Extra filters, limits, custom timestamp transforms, alternate
validators and unfamiliar source shapes block certification. Do not rewrite copied
source to make it match. Recognized shape plus a mandatory full-path review is
required; upstream source or a checked box alone is insufficient.

Review the actual `frappe.db.get_values`/query implementation and confirm that the
endpoint's filter is equivalent to parameterized database-column equality with no
status/permission/tenant filter or limit. The exporter performs that equivalent
SELECT against `tabEmployee`, with the database's own collation and independent
COUNT for each exact lookup string. It preserves all matches, including inactive
employees, and exports only employee name/ID, attendance_device_id and status.
`mapping_resolution='frappe.db.get_values'` records these reviewed semantics; the
exporter intentionally does not execute Frappe's runtime function.

From the proven site's effective runtime hooks, prepare this JSON structure. Values
must reflect the complete actual inventory, including wildcard hooks:

```json
{
  "schema": "frappe-effective-hooks-v1",
  "installed_apps": ["REPLACE with the actual app list"],
  "override_whitelisted_methods": {},
  "override_doctype_class": {},
  "extend_doctype_class": {},
  "doc_events": {}
}
```

An authorized administrator can inspect `frappe.get_installed_apps()` and the four
`frappe.get_hooks(...)` values in an existing administrator context, without invoking
the endpoint or hooks. Do not create/install a Server Script for this task. Preserve
all values and compare with deployed app revisions; `{}` is valid only when the
actual inventory is empty for that key. Relevant method/controller overrides,
extensions, Employee Checkin document hooks and wildcard document hooks are currently
unsupported and block, even when someone marks them reviewed. Unrelated hooks may
remain in the inventory. Enabled Employee Checkin DocType Event Server Scripts and
API scripts colliding with the endpoint also block.

The exporter reads enabled Employee Checkin event/API Server Script metadata using
the fixed `SQL['scripts']` query in `scripts/frappe_export_database.py`; script bodies
are hashed **inside the database** with SHA2 and never exported/transmitted. Obtain
that same ordered metadata result separately during review and set
`server_scripts_sha256=fingerprint(rows)`. No rows means `fingerprint([])`.
The live snapshot must match it exactly; unrelated API-script changes also invalidate
the review. Record installed app revisions/full relevant source review in the
restricted evidence record. Database metadata cannot verify Python files on disk;
the administrator must verify copied files actually belong to that deployment and
freeze them through export. If provenance is uncertain, leave the review incomplete.

For policies, use a protected, non-secret `integration-policy.py` containing literal
`ERPNEXT_URL`, `ERPNEXT_VERSION`, `devices` entries with only device_id/punch_direction,
and any configured `device_punch_values_IN`/`device_punch_values_OUT`. Derive it from
the actual integration configuration, independently review it and pin
`fingerprint(literal_policy(path))`. Missing code lists use the reviewed engine's
defaults [0,4]/[1,5]; missing direction means None. AUTO applies OUT before IN, as in
the existing engine. The exporter checks this sanitized policy against the approved
digest. It can read the original literal config but never imports it or evaluates
password/API settings; prefer the sanitized policy to avoid moving secrets.
Dynamic settings/configuration overlays or unsupported loading behavior must block
manual approval rather than treating AST-extracted literals as runtime proof.

## Approval artifact and connection profile

Start from [verification.template.json](../examples/frappe-export/verification.template.json).
Every verification field starts **false**. Fill source hashes, authoritative database
identity digest, source-derived semantics, actual timezone/policy, current aware ISO
`reviewed_at`, short `expires_at`, and the restricted evidence reference. Set a
verification field true only after performing its corresponding review above.
An independent authorized reviewer approves and records the final JSON's SHA-256
out of band. Pass that trusted hash to the CLI. Re-hashing an unreviewed/replaced
bundle immediately before use does not establish provenance. The tool cannot
authenticate the truth of a forged administrator attestation; approval custody and
protected inputs are part of the trust boundary.

Use [db-profile.template.json](../examples/frappe-export/db-profile.template.json) for
host, port, database, existing SELECT-only username and absolute TLS CA path.
Passwords are forbidden in this profile and on the CLI: live mode prompts with
`getpass` and fails if secure prompting is unavailable. No credential is stored in
outputs/logs. Protect the profile and review bundle despite their minimal contents.
Pin `database_profile_sha256=fingerprint(profile)` and the CA's byte SHA-256 in the
approved review as well. Both are checked before prompting or connecting, preventing
a changed profile/CA from redirecting authentication to an unapproved server.
The driver enforces CA verification and TLS hostname verification and disables local
infile/default configuration/init commands. Do not disable certificate validation.
The live connector requires exactly PyMySQL 1.2.3, whose explicit TLS options reject
a server without TLS before authentication ([pinned driver source](https://github.com/PyMySQL/PyMySQL/blob/v1.2.3/pymysql/connections.py)).
An older/unreviewed driver blocks; installing the pinned version is a separate
workstation setup step, not a production service dependency change.

The account must have direct SELECT privileges on exactly the five needed tables
(`tabEmployee`, `tabEmployee Checkin`, `tabSingles`, `tabServer Script`,
`tabDefaultValue`), or SELECT on the proven site's schema. Only global USAGE is also
accepted. Roles, column grants, write/administrative/FILE/EXECUTE privileges, grant
option, other schemas and global SELECT are rejected. Self `SHOW GRANTS` and
information_schema visibility must work. There is no permission-limited ORM fallback.

## Exact commands for a future authorized export

Use a separate Python 3.10+ review-workstation environment, outside the production
service environment. `requirements-export.txt` pins the optional PyMySQL dependency;
tests/plan do not require it. Install it into that separate environment only after
normal dependency approval. Native execution is currently Windows: output ACL
validation reuses the existing local NTFS guard, without querying SCM. Non-Windows
output ACL checks fail closed.

Copy the reviewed standalone scripts bundle to the workstation: new export CLI,
`frappe_export_review.py`, `frappe_export_database.py`, plus the unchanged
`attendance_reconciliation.py`, `reconcile_attendance.py` and
`capture_history_safety.py`. No production app installation or service deployment
is necessary. Use protected, verified **copies** of capture evidence; never point
the command at the original production evidence directory during development.

Provision an existing empty local NTFS output directory outside the checkout,
production state roots and all input/review/profile directories. Disable inherited
ACLs and allow only the operator/approved administrators/SYSTEM; audit custom groups.
The CLI checks ACLs, write access and at least 256 MiB free without changing ACLs.
Do not place unrelated files there. Keep inputs equally protected and immutable.

After separate approval and artifact preparation, replace all placeholders:

```powershell
$PythonExe = 'C:\protected\frappe-export\venv\Scripts\python.exe'
$Tool = 'C:\protected\frappe-export\scripts\export_frappe_attendance.py'
$Capture = 'C:\protected\frappe-export\inputs\capture_summary_APPROVED.json'
$CaptureHash = '<independently recorded original capture-summary SHA-256>'
$Policy = 'C:\protected\frappe-export\review\integration-policy.py'
$Review = 'C:\protected\frappe-export\review\frappe-export-verification.json'
$ReviewHash = '<independently approved verification JSON SHA-256>'
$Profile = 'C:\protected\frappe-export\review\db-profile.json'
$Output = 'C:\protected\frappe-export\exports\approved-run-001'
```

Offline plan (no database/credentials/files):

```powershell
& $PythonExe -B $Tool --capture-summary $Capture --capture-summary-sha256 $CaptureHash --integration-config $Policy --verification $Review --verification-sha256 $ReviewHash --output $Output --start '2026-10-11T00:00:00' --end '2026-10-15T23:59:59.999999' --plan
if ($LASTEXITCODE -ne 0) { throw 'Plan blocked; resolve evidence/permissions before export' }
```

Only for the separately authorized live export, enter the read-only DB password at
the secure prompt (do not pipe or embed it in commands):

```powershell
& $PythonExe -B $Tool --capture-summary $Capture --capture-summary-sha256 $CaptureHash --integration-config $Policy --verification $Review --verification-sha256 $ReviewHash --db-profile $Profile --output $Output --start '2026-10-11T00:00:00' --end '2026-10-15T23:59:59.999999' --page-size 1000 --live-export --operator-authorized
if ($LASTEXITCODE -ne 0) { throw 'Export failed; no certified manifest may be used' }
Get-ChildItem -LiteralPath $Output -File | Get-FileHash -Algorithm SHA256
```

Record stdout's `manifest_sha256` out of band, verify the three final file hashes,
then use the existing reconciliation CLI in a separately approved offline run:

```powershell
$Reconcile = 'C:\protected\frappe-export\scripts\reconcile_attendance.py'
$ExportHash = '<verified exporter manifest_sha256 from the successful run>'
$Reports = 'C:\protected\frappe-export\reconciliation-reports\approved-run-001'
& $PythonExe -B $Reconcile --capture-summary $Capture --capture-summary-sha256 $CaptureHash --exports-manifest (Join-Path $Output 'export-manifest.json') --exports-manifest-sha256 $ExportHash --output $Reports --start '2026-10-11T00:00:00' --end '2026-10-15T23:59:59.999999' --site-timezone 'Asia/Riyadh'
if ($LASTEXITCODE -ne 0) { throw 'Offline reconciliation failed' }
```

These commands were documented/parsed, not executed against production. No command
above stops/starts/reconfigures a service or creates/updates Employee Checkins.

## Completeness, output and failure rules

Capture hashes/manifests are validated first. Every exact nonempty usable user ID
in the selected period is resolved; uncertain records retained by reconciliation
also contribute usable IDs, conservatively. Malformed/oversized/missing IDs remain
ambiguous. Include all employee statuses and preserve multiple/no matches.

Both mappings and Checkins come from ONE transaction/connection. The Checkin WHERE
clause has only the inclusive time range, and pagination uses database-ordered name
keysets, not offsets. There is no employee/device/log-type/attendance/permission
filter. A separate COUNT before pagination, terminal empty page, unique names,
exported row count and final independent COUNT must agree in that same snapshot.
Snapshot state is rechecked after mappings and Checkins. Concurrent ordinary data
writes remain outside the snapshot. Unsupported storage engines/views fail.

Time values are exact naive site-local datetime strings with six fractional digits;
blank device/log type remain blank. The manifest's precision describes the reviewed
server write semantics; existing fractional Checkins are never rounded/discarded.
Coverage start is widened to a whole second when the verified server truncates.
The transaction start is bracketed by UTC database-clock queries immediately before
and after START; the manifest records both bounds. `exported_at` is the conservative
lower bound, which must be at/after both capture finish timestamps.
The exporter buffers bounded results (at most 500,000 Checkins, 100,000 matches per
lookup, existing input limits 512 MiB per file); provision adequate workstation RAM
and disk. Limits block rather than silently truncate. Mapping/Checkin data describes
this later snapshot, not a reconstructed historical employee mapping.

The manifest follows the existing reconciliation schema exactly and adds only
provenance/completeness evidence: authoritative URL, review/source-reference digest,
capture hashes, column-metadata digest, lookup/match counts, independent Checkin
counts, page counts, transaction mode, privilege/storage checks and rollback result.
Verification booleans become true only after those checks and trusted source/site
review succeed. The existing `load_exports` validates the generated files before
the final manifest is atomically published, without changing the reconciliation tool.
The source reference resolves to the protected review bundle; keep it with the
incident record. Console output includes counts/hashes only, never employee IDs,
Checkin names, DB profile values, passwords or raw exceptions.

Files use flush/fsync and no-overwrite publication; a nonempty output directory is
rejected before connection. The manifest is published last. On failure or cleanup
error, return nonzero and issue no certificate. Protected partial/orphan files may
remain; do not reuse them or overwrite evidence. Fix the cause and choose another
empty approved directory. Retain/restrict files under the incident's approved policy;
deletion requires the data owner's authorization. No source evidence is changed.

The CSV is exact machine input and may contain spreadsheet formula-like identifiers.
Do not open/resave it in Excel; reconciliation's separate detail report escapes such
cells. Protect exports and backups as employee data. No API tokens, device passwords,
database passwords, source code, hook script bodies or unnecessary personal fields
are exported. The review itself is an external trust dependency: dishonest or stale
hosting/source approval cannot be disproven by database counts alone.

## Development checks

```powershell
py -m unittest discover -s tests -p test_frappe_attendance_export.py -q
py -m unittest discover -s tests -q
```

Tests use synthetic captures, fake database cursors/snapshot state, mock TLS/profile
connectors and mock ACLs only. They exercise complete/duplicate/inactive mappings,
leading zeros, cross-device and blank-device Checkins, fractional timestamps, source
policy inference, hooks/overrides, authority binding, independent counts, concurrent
writer simulation, truncation, permissions/no-write enforcement, output/hash failures
and compatibility with the existing offline loader. A fresh-process import guard
proves plan mode cannot import network/Frappe/engine/config/SQLite modules. No real
MariaDB, production TLS/ACLs, deployed source, authoritative route or live site was
validated during development; those are future acceptance requirements.

October 15, 2026 verification: **239 tests passed** (185 existing + 54 exporter
tests) in 66.062 seconds. **31 Python files** syntax-compiled without configuration
imports; all **five PowerShell blocks** in this procedure parsed without execution.
No database dependency was installed, and no live export or production operation
was performed. The implementation report supplies the published feature commit.
