# Phase 1 server-side attendance export

This adds a server-local transport to the existing [export workflow](phase1-frappe-export-procedure.md).
It produces the same three reconciliation inputs with the same hash/count/source
checks. The original TLS database exporter remains available with its original
default behavior. No public endpoint, Frappe app installation, Server Script or
File document is introduced.

The operator confirmed `https://erp.myfpf.com` and `myfpf.v.frappe.cloud` refer to the
same site. This confirmation is accepted; no new DNS/MCP investigation is required
for the alias relationship. The approved internal site directory, backend identity
digest and deployed source still need to match the administrator's review artifact.
Development did not access either site, production evidence or a real database.

## Transaction review and execution choice

The reviewed upstream Frappe [v15 database implementation](https://github.com/frappe/frappe/blob/version-15/frappe/database/database.py)
supports a read-only begin, but its commit/rollback helpers start another transaction.
Ordinary independent ORM queries therefore do not establish the required snapshot
boundary. The [v14 command wrapper](https://github.com/frappe/frappe/blob/version-14/frappe/commands/utils.py)
and [v15 command wrapper](https://github.com/frappe/frappe/blob/version-15/frappe/commands/utils.py)
for `bench execute` initialize a site connection and commit on completion. Console
execution also has framework lifecycle and history/logging behavior. None provides
the separate, SELECT-only connection and private-output guarantees required here.

Use an approved administrator shell/job in the **existing Bench Python environment**:
`<bench>/env/bin/python -B <reviewed-tool> ...`. This is the hosting-administrator
execution mechanism; do not use `bench execute`, a Frappe console, an API call or
the writable `frappe.db` connection. The utility rejects an already imported Frappe
runtime, does not read `site_config.json`/`db_password`, and imports no Frappe app.
It reads deployed source as data rather than running import-time app code.

The disposable connection uses a separately approved SELECT-only account over the
local MariaDB Unix socket. No external MariaDB access or TCP port is needed.
Permissions are verified using the original grant checker, followed by
`REPEATABLE READ` and [START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY](https://mariadb.com/docs/server/reference/sql-statements/transactions/start-transaction).
Session state is checked after beginning, employee resolution and Checkin reading;
all relevant tables must be InnoDB base tables. Clock queries bracket the start.
The same connection performs both lookup/Checkin SELECTs and independent counts.
No ORM, cache, permission-filtered fallback, reconnect or transaction reset occurs.
After reading, raw connection rollback/close is required before file certification.
Review expiry is checked before each snapshot query as well as before publication.

## Supported hosting and trust boundary

| Environment | Result |
| --- | --- |
| Linux Bench/admin shell, local MariaDB Unix socket and approved SELECT-only account | Supported after deployment/source/permission review. |
| Hosting administrator can run the same private job with the Bench environment and socket mounted | Supported subject to the identical checks. |
| App container has only a remote/private TCP database and no accessible local socket | This transport blocks. Ask the hosting administrator for an approved compatible execution location; no writable-account fallback. |
| Frappe Cloud tenant has no shell/job execution or read-only account available | Blocks. A separately reviewed authenticated endpoint may be considered later; none is added here. |
| `bench execute`, console, browser Server Script, public API, Windows server runtime | Not an execution route for this transport. |

The implemented compatibility boundary is Frappe v14/v15 source layout and MariaDB
10.6/10.11/11.4, with actual supported source shape still checked by the existing AST
validator and administrator review. Other versions block rather than assume equivalent
behavior. No claim is made that production currently uses one of these versions.
The Bench environment must already contain PyMySQL 1.1.1, 1.1.2 or 1.2.3; no dependency
is installed/upgraded automatically. Frappe v15's reviewed dependency declaration
[pins PyMySQL 1.1.1](https://github.com/frappe/frappe/blob/version-15/pyproject.toml).
Those versions use the same local Unix socket constructor. The original external
TLS transport's stricter 1.2.3 requirement is unchanged. Local socket mode explicitly
disables TLS because it does not use TCP; it authenticates the daemon with Linux
kernel peer credentials and then verifies the approved database identity.

The socket must be canonical, owned by the approved non-root MariaDB daemon UID,
and reached through root/daemon-owned directories without group/world write access.
The connected socket must be AF_UNIX at that exact path, with the expected UID from
[SO_PEERCRED](https://man7.org/linux/man-pages/man7/unix.7.html). Device/inode identity
is rechecked to catch socket replacement. The SELECT-only account is still required;
OS peer credentials do not substitute for SQL privilege validation. Use an existing
approved account; DBA account provisioning is separate authorization, not part of
this development task. If a supported safe account/context is unavailable, stop.

## Review artifacts and actual deployed behavior

All original `verification.template.json` trust fields, hashes, expiry, source
review and database identity checks remain required. The new `server_internal`
field defaults to null, so it cannot accidentally authorize a server job. Copy the
contents of [server-internal.template.json](../examples/frappe-export/server-internal.template.json)
into that field only after performing the corresponding administrator review.
The operator's confirmed aliases are listed, but do not replace backend/source checks.

The server block binds the canonical Bench root, exact internal site directory,
Frappe major version and non-secret internal profile digest. Use
[internal-profile.template.json](../examples/frappe-export/internal-profile.template.json),
replacing the socket, database, SELECT-only username and actual MariaDB UID. UID 0,
extra keys, passwords, host/port overrides and the legacy `--db-profile` option are
rejected by this entry point. The profile digest is the existing
`frappe_export_review.fingerprint(profile)`, not the raw JSON file hash.
The original TLS profile/CA fields are unused by this separate transport; they must
not be treated as performed TLS checks or used to approve a server-local connection.

Use the already documented deployed-source review for endpoint lookup, duplicate
rules, timestamp precision, overrides, inherited validators and hooks. The server
transport additionally reads these **actual files in the approved Bench** and checks
them against the approved hashes before connecting and again after reading:

- Resolved `hrms`/`erpnext` Employee Checkin endpoint/controller module.
- `frappe/database/database.py` and `frappe/database/query.py`.
- `frappe/__init__.py`, including its literal `__version__` and approved hash.
- `<app>/hooks.py` for **every installed app** in the effective-hooks inventory.

No app imports are used to infer effective hooks. `runtime_hooks_reviewed=true`
attests that an administrator checked the actual site's complete effective hooks,
including dynamic/conditional hooks and wildcard events, and bound them to these
files/deployment. If runtime behavior cannot be established, leave it false and
stop. Relevant method/controller overrides, wildcard/Employee Checkin hooks and
colliding/enabled Server Scripts retain the original fail-closed restrictions.
Live installed-app and Server Script inventories must match the reviewed snapshot.
Independent source hashes detect file changes; the administrator's deployment freeze
and provenance review remain necessary to rule out arbitrary runtime monkey patches.

Production punch policies remain tied to the trusted sanitized integration policy
and reviewed Windows integration source; there is no attempt to read the running
Windows service, connect to devices or import its configuration on this server.
The operator must provide the already approved policy/evidence copies. Hash the
completed verification JSON and record that approval hash independently before use.
No verification flag is automatically enabled merely because aliases are confirmed.

## Private server storage

Prepare the reviewed utility bundle and protected input/review copies through an
approved administrator mechanism; copying them is not a Frappe app deployment.
Use a dedicated directory outside the Bench (including all `sites/*/public`,
`sites/*/private`, assets/backups) and outside the tool bundle. The existing output
directory must be empty, canonical, owned by the executing OS account and exactly
mode 0700. Ancestors must be root/operator-owned without group/world write access;
symlink paths and shared `/tmp` ancestry are refused. Do not loosen a failed check.
Minimum free space is 256 MiB; provision enough for full exports and memory bounds.

The process sets/restores umask 0077. Temporary files use exclusive creation with
0600 permissions, flush/fsync, same-directory no-overwrite finalization and directory
sync. The three outputs are private files, not Frappe documents or attachments.
Do not open the exact-input CSV in a spreadsheet; use the reconciliation report for
review. Retain/restrict exports, partials, inputs and review artifacts under the
incident policy. No passwords/tokens/source/hook bodies enter the manifest or console.

## Exact authorized execution instructions

These are future administrator commands, not commands executed in development.
Obtain explicit export approval, the SELECT-only credentials and completed review
first. No production action is authorized by this implementation task. Place this
release's `scripts/` bundle in the approved private tool location; it needs the two
new server modules and the existing export/reconciliation/safety modules. Use the
Bench interpreter directly without installing a Frappe app or changing dependencies.

Sample shell variables (replace the Bench path/site directory with the approved values):

```bash
BENCH=/home/frappe/frappe-bench
PYTHON="$BENCH/env/bin/python"
TOOL=/home/frappe/admin-attendance-tools/scripts/export_frappe_attendance_server.py
SITE=myfpf.v.frappe.cloud
CAPTURE=/home/frappe/attendance-private/inputs/capture_summary_APPROVED.json
CAPTURE_HASH='<independently recorded original capture-summary SHA-256>'
POLICY=/home/frappe/attendance-private/review/integration-policy.py
REVIEW=/home/frappe/attendance-private/review/frappe-export-verification.json
REVIEW_HASH='<independently approved verification JSON SHA-256>'
PROFILE=/home/frappe/attendance-private/review/internal-profile.json
OUTPUT=/home/frappe/attendance-private/exports/run-001
```

The administrator provisions the empty private output and audits ancestors beforehand,
for example using `install -d -m 0700 "$OUTPUT"` under the approved OS account. Do not
use a web-served Bench path, shared temporary directory or a writable database account.
No SQL grant/user creation command is part of this procedure.

Default offline plan, which only validates local evidence/source/storage and does
not authenticate/query the database:

```bash
"$PYTHON" -B "$TOOL" --bench-root "$BENCH" --site "$SITE" --internal-profile "$PROFILE" --capture-summary "$CAPTURE" --capture-summary-sha256 "$CAPTURE_HASH" --integration-config "$POLICY" --verification "$REVIEW" --verification-sha256 "$REVIEW_HASH" --output "$OUTPUT" --start '2026-10-11T00:00:00' --end '2026-10-15T23:59:59.999999' --plan
```

Only after separate live-export authorization, run the following and enter the
read-only database password at the secure prompt. Do not pipe it or place it in
shell arguments, environment variables, Frappe console history or profile files.

```bash
"$PYTHON" -B "$TOOL" --bench-root "$BENCH" --site "$SITE" --internal-profile "$PROFILE" --capture-summary "$CAPTURE" --capture-summary-sha256 "$CAPTURE_HASH" --integration-config "$POLICY" --verification "$REVIEW" --verification-sha256 "$REVIEW_HASH" --output "$OUTPUT" --start '2026-10-11T00:00:00' --end '2026-10-15T23:59:59.999999' --page-size 1000 --live-export --operator-authorized
status=$?
test "$status" -eq 0 || exit "$status"
sha256sum "$OUTPUT/employee-mappings.json" "$OUTPUT/employee-checkins.csv" "$OUTPUT/export-manifest.json"
```

Record stdout's manifest hash out of band. Transfer the three unchanged files via an
approved secure administrator channel, verify hashes after transfer, and use the
existing [offline reconciliation instructions](phase1-attendance-reconciliation.md).
That tool accepts this manifest without a new schema or transport-specific bypass.
The inputs retain all employees/devices, inactive/duplicate mappings, blank device
IDs/log types and exact fractional timestamps; date filtering/counting is unchanged.

## Failure, cleanup and completeness

Independent counts, terminal empty page, unique Checkin names, expected row totals,
snapshot state, grants, metadata and source inventories must all pass. A successful
export issues the same certification flags plus transport provenance: no external
database access, no Frappe connection, kernel peer proof, supported MariaDB series,
private storage, deployed-file verification and opaque context/profile/socket hashes.
No employee identifiers or private host paths are printed in console summaries.

On a read, permission, source, socket, private-storage, hash, write or cleanup failure,
exit nonzero and do not use orphan/partial files as certified exports. The transaction
is rolled back/closed even after an exception; no commit or framework observer runs.
Source files and output permissions are rechecked before publication; review expiry
is enforced again before the final manifest. Choose a new approved empty directory
for retries; never overwrite evidence. Failed connection/rollback cleanup may require
the hosting administrator to inspect/close that dedicated read-only session. No
Windows service/device/recovery change is part of failure handling.

Tests run on synthetic Bench trees, fake database connections and mocked Linux socket/
owner/permission data. No real Linux peer credentials, production ACLs, MariaDB
snapshot, hosting mechanism, runtime hook inventory or deployed source is live-proven
by development tests. Complete those acceptance checks in the authorized environment
before relying on an export. Certification still depends on trustworthy administrator
review; the utility does not claim to prove arbitrary Python runtime behavior.

Development verification on October 15, 2026: **271 tests passed** (239 existing
and 32 server-side tests) in 40.079 seconds. All **34 Python files** syntax-compiled;
the **three Bash command blocks** passed `bash -n` without execution, and all four
export JSON templates parsed. Synthetic server exports passed the existing
reconciliation CLI. Original TLS-export tests passed unchanged. No live Frappe,
MariaDB, device, hosting or Windows service operation was performed.
