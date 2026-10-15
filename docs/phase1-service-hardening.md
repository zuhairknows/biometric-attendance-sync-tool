# Phase 1 Windows service hardening — development candidate only

Feature-branch publication is authorized. Production deployment, service restart,
historical recovery and stable-branch merges remain unauthorized. The branch is `feature/phase1-service-hardening`,
based on Phase 1 `478be326`. Production source baseline remains `a23b8112`.

## What changes

The existing service name, registration, direct Python hosting, SDK polling, device
connection settings, timestamp values, direction policies and insertion endpoint remain
unchanged. Durable mode is still disabled by default. No GUI, device discovery, worker
pool, new database schema, recovery executor or historical replay option is introduced.

The service now requires explicit offline provisioning before durable startup. It never
creates a missing database automatically. `durable_startup.py` supplies these small guards:

- A Windows global named mutex allows one cooperating durable worker across sessions and
  database paths. Service and console loops retain it during idle waits; individual calls
  to the engine also guard their cycles. Provisioning takes the same mutex. Kernel ownership is released when a
  process dies. This does not control an old, unmodified legacy worker: operations must
  still ensure no legacy GUI, scheduled job or second installation is running.
- The database must be in an existing private local NTFS directory outside the checkout
  and known OneDrive paths. No UNC storage, reparse entries or unrelated file owners/ACL
  grants are accepted. Directory inheritance must be disabled. Allowed principals are
  the service identity, SYSTEM and Administrators; provisioning may name the existing
  custom service SID. Permissions are checked, never changed by the application.
- A private `<database>.activation.json` binds a random SQLite `application_id`, the
  hashed destination/endpoint family, exact local cutover timestamp and review reference.
  No credentials or URL are stored in that marker. Missing/invalid markers, missing or
  replaced databases and destination changes block polling and delivery. Restarts do not
  infer a new boundary. Do not edit the marker to bypass a failed guard.
- Provisioning accepts only a new or verified empty unbound ledger and an explicit future
  local cutover time. Nonempty ledgers require a separately reviewed migration; they are
  never silently adopted, reset or marked successful. Existing databases are inspected
  read-only before any write-mode SQLite setup. Schema remains version 1.

Private storage and a trustworthy local clock remain required. Administrator tampering,
unknown cloud-sync products and restoring an older backup with the same application ID
cannot be reliably detected by this cookie. A restore always needs interval accounting.
Both the latest ledger and its marker must be preserved together.

## Cutover behavior

The live boundary is the later of the provisioned cutover and existing `IMPORT_START_DATE`.
Both are inclusive. Payload timestamps are never changed. Captures before that boundary
are retained with their source identity, evidence and original payload as `rejected` with
`error_category: cutover_hold`. They are not submitted, discarded or marked delivered.
The delivery path also holds older pending records from an explicit legacy import, so
importing a dump cannot bypass the boundary. No automatic dump replay/deletion is added.

Invalid records retain their existing validation quarantine. Repeated captures preserve
their original identity and state. Held historical records remain unresolved and cap Shift
Type watermarks; this may intentionally prevent watermark progress. There is no automatic
release, mass requeue or conversion of held history to success. Resolving historical gaps
or changing that watermark policy requires separate review and authorization.

## Uncertain destination outcomes

An HTTP timeout, 408/5xx, unfamiliar response, incomplete 200 response or expired delivery
lease may hide a remote commit. Such records keep their durable retry state, but another
POST is forbidden without resolution. A duplicate error string alone is also insufficient
to mark a record complete. Repeated confirmation attempts use persisted backoff/cooldown.
An unresolved local confirmation holds that row without imposing destination-wide cooldown
on unrelated fresh punches. Actual transport/authentication/rate failures still pause the
destination as before.
Another queued record with the same biometric ID and second is also held while a peer's
outcome is uncertain, across device IDs, SDK UIDs and directions. This conservative guard
does not guess employee identity from UID or resolve different biometric aliases to the
same employee; deployed mapping/duplicate semantics still require administrator review.
These unsent peer holds use a distinct category and can make their first submission once
the uncertain peer is resolved. They are not falsely treated as previously submitted.

Known authentication refusals (401/403) and rate-limit refusals (429) retain automatic POST
retry after cooldown. Staging must verify these deployed responses occur before insertion.
Other uncertain records only perform read-back confirmation. A positive exact identity
can complete them as `duplicate` with the confirmed Checkin name. Empty, filtered, ambiguous,
failed or unavailable reads never authorize a repeat POST. There is deliberately no
automatic "confirmed absent -> resubmit" path: an earlier request might still commit.

Confirmation is disabled unless BOTH operator settings are present:

```python
DURABLE_CONFIRM_READS_VERIFIED = False
DURABLE_SERVER_REVIEW_REFERENCE = ""
```

These examples intentionally do not attest anything. Do not change them to true/nonempty
until an administrator has reviewed actual deployed employee resolution, timestamp
truncation/timezone, null-to-blank handling, duplicate rules, hooks/overrides/Server Scripts,
and unfiltered read permissions. An offline flag cannot prove that review took place.
Changing these settings requires the normal separately approved service cutover procedure.

With reviewed reads enabled, confirmation queries Employee by exact `attendance_device_id`
and requires one active match, then queries Employee Checkin by employee, second-truncated
comparison timestamp and expected log type across ALL devices. Two returned rows suffice
to detect ambiguity. SQL NULL returned in JSON is never accepted as an empty-string match.
Only comparison time is truncated; the queued/submitted payload is unchanged. Confirmation
uses bounded GETs, shares delivery pacing and honors stop requests between reads. Redirects
are disabled for both GET and POST, so a routing change becomes a visible failure.

Uncertain records can therefore require administrator investigation after an outage even
when the server is healthy again. This is a deliberate safety limit, not silent data loss.
The program does not guarantee exactly-once remote execution. Initial endpoint behavior
still requires deployed-source/runtime staging validation before production activation.

## Operational diagnostics

Existing log formatting is retained. JSON message bodies identify `durable_capture`,
`durable_scan_failed`, `durable_delivery`, `durable_cutover_hold`, `durable_health` and
`durable_critical` events. Critical failures never turn into a successful cycle; the Windows
service may still report Running, so health monitoring must inspect these events.

`sync_cli.py --db <absolute-path> status` opens SQLite read-only and reports:

- Existing aggregate state counts and pending counts per device.
- `backlog_age_seconds`: age since the oldest unresolved queued record was captured,
  rather than the age of its possibly historical device timestamp.
- `failed_scans`, `quarantined`, `held_historical`, `uncertain_submissions`, `held_related_records`.
- `activation_present`: presence only, not proof of destination/configuration consistency.

Health messages contain aggregate counts, fixed categories and source/device or ledger
record IDs; they do not include employee identifiers, payloads, URL, credentials or raw
exception/server bodies. Storage failures may prevent database-based diagnostics; retain
and monitor the structured critical event and existing error logs. Logging storage itself
must be monitored externally if its disk becomes unwritable. No health sidecar is written
as a fallback that could accidentally look successful.

## Future approved provisioning and deployment

The older cutover runbook predates these guards. Do not use its activation instructions
unchanged or deploy its old commit pin as this candidate. Before deployment:

1. Review the published feature candidate; pin its final approved commit only after tests pass.
2. Verify actual Windows account, Python/SQLite/SDK versions, local timezone, absolute paths,
   device configuration, server behavior and absence of other workers on staging.
3. Under separate maintenance approval, stop the old service gracefully and preserve exact
   source/configuration, logs, status, original dumps and current ledger/marker if present.
   Do not reinstall the service, clear device history, change credentials or alter devices.
4. Provision an administrator-controlled dedicated NTFS directory with only SYSTEM,
   Administrators and the unchanged service account. Run the new CLI against a NEW/EMPTY
   ledger. Choose a future local boundary that covers the intended live start without
   authorizing historical recovery; explicitly account for the maintenance interval.

Prepared command shape, **not executed**:

```powershell
py -B .\sync_cli.py --db '<approved-absolute-new-ledger-path>' provision `
  --destination-url '<approved-existing-site-url>' --erpnext-version 15 `
  --cutover-time '<approved-future-YYYY-MM-DD HH:MM:SS>' `
  --review-reference '<approved-cutover-review-reference>' --acknowledge-cutover
# For an existing custom service account, also pass --service-sid '<existing-account-SID>'.
```

No credentials are accepted by this command; it never contacts Frappe or devices. A failed
provision can leave a bound database or partial marker; startup then fails closed. Preserve
those files and investigate instead of deleting/recreating them. Provisioning is not a
general-purpose database-adoption or restore command.

5. Verify the new ledger and a usable protected backup, preserve its activation marker,
   deploy the reviewed modules together with durable mode still disabled, and enable only
   after separate approval. Do not change the existing service registration/interpreter.
6. Observe multiple complete cycles: captured records persist before sends; failures remain
   visible; no old punches are POSTed; queue age resolves; device cleanup and stop bounds
   are acceptable; quarantines and Shift Type watermark behavior are understood.

The boundary can leave an interval before live activation held for review. Keep the old
worker operating until the approved stop time where possible, minimize the maintenance
window, and explicitly reconcile the remaining gap. Never backdate provisioning to hide it.

For rollback, stop safely and preserve the LATEST ledger, activation marker and backups.
Prefer a reviewed durable-compatible repair. Do not restore an old snapshot over the
latest queue, create an empty replacement or resume legacy while SQLite-only records are
unaccounted for. A lost marker, nonempty old ledger, identity mismatch or backup restore
requires a separate migration/recovery review; no rebind/force switch is supplied.

## Tests and Windows staging gates

Final development result: **335 tests passed with no exclusions**, including **24 focused
hardening tests**, **40 diagnostic tests** and the ten loopback HTTP scenarios. CLI `--help`
and `git diff --check` passed. The complete run used:

```powershell
py -B -m unittest discover -s tests -q
```

The four prior diagnostic failures were fixture errors: tests copied the working source
while expecting approved `478be326` hashes. Fixtures now read the exact immutable Git blobs
and verify every existing profile hash before testing. Production `a23b8112` hashes remain
separately verified. No pinned source hash was replaced and no allowlist was relaxed. A
regression proves the hardened worktree does not inherit old-profile certification. The
standalone diagnostic is published with its existing reviewed bytes and stable transfer
hash; its source-profile refusal is still expected against this new service implementation.
Both pinned commits must exist in local Git history for the tests; no production connection
or configuration import is involved.

Final review also corrected durable console interruption so Ctrl+C/SystemExit releases
worker ownership rather than being swallowed. Legacy console behavior is unchanged.
Invalid destination versions, ports and whitespace are rejected before polling.

Focused synthetic tests cover provisioning, held history, payload preservation, startup
refusal, changed destinations, worker contention/process death, uncertain outcomes,
expired leases, duplicate phrases, NULL/blank, mapping ambiguity, failed permissions,
read-only diagnostics, structured failures and continuation after one failed device.
Existing durable, release, legacy, capture, export and loopback tests must also pass.

Production Windows staging must still prove real private ACLs/custom service SID access,
global mutex behavior under the service identity, disk-full/corruption/missing-ledger refusal,
backup restoration/accounting, clock correctness, device cleanup, stop latency during large
captures/in-flight HTTP, real Frappe response formats, unfiltered read permissions and runtime
hooks. No production, real-device, Frappe-hosting or SCM restart validation was performed.

Before approving Windows staging, record evidence for every gate below. A failed gate
blocks production activation; development tests do not substitute for these observations.

| Gate | Required staging evidence |
| --- | --- |
| Service identity and compatibility | Unchanged name/registration/interpreter, SDK patch/device settings, exact payload direction/null and timestamp behavior, and existing endpoint family |
| Worker ownership | Contention across processes, sessions and different DB paths; exclusivity during idle waits; graceful stop and recovery after process death under the actual service account |
| Storage and identity | Dedicated local NTFS, approved file owners/ACLs including custom service SID, no cloud/reparse storage; refusal on missing/replaced DB, missing marker, destination mismatch, corrupt schema or disk-full |
| Cutover | Explicit future local boundary, correct device/Windows clocks, held counts and original payloads preserved across restart, zero pre-boundary POSTs, no dump changes, maintenance gap accounted for |
| Destination confirmation | Actual hooks/overrides/scripts, mapping uniqueness, unfiltered read access, NULL/blank and timestamp behavior reviewed; lost response/lease/5xx/empty or denied reads cause no blind POST; related holds do not stall unrelated fresh work |
| Refusal retries | Deployed 401/403/429 responses are verified to occur before insertion; bounded cooldown/rate/Retry-After behavior verified |
| Watermarks and operations | Held/quarantined records cap advancement, remote/local starting watermarks audited, failed scans and critical errors alert even if SCM says Running, backlog age monitored |
| Recovery and rollback | Latest DB plus activation marker backed up and restored with interval accounting; no automatic nonempty-ledger adoption/rebind, no empty replacement and no stranded SQLite-only work on legacy rollback |

The existing standalone diagnostic intentionally recognizes only its reviewed old source
profiles. Use manual approved source/hash evidence for staging this candidate, or obtain
a separately reviewed new diagnostic profile. Do not bypass its refusal or reinterpret
literal settings output as proof of loaded service memory or deployed server behavior.
