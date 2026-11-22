# Stage 2: service-owned recovery backend (synthetic validation only)

> Archived development evidence through `29a440c`. The later
> [historical recovery development report](historical-recovery-development.md)
> and [release procedure](historical-recovery-deployment.md) supersede the
> synthetic-only service gate and transport limitations described below.
> Production deployment and activation still require independent approval.

**Production execution is disabled in code. Do not deploy or change production
configuration.** The Flask manager retains its investigation-only restrictions;
there are no new execution routes or controls. Stage 1 previews, offline reports
and production compatibility-only reports cannot authorize recovery.

## Backend interface

`historical_attendance.service_handoff` provides the manager-side operations:

1. `prepare_request(inbox, live_ledger, ...)` reuses Phase 2B's full Phase 2A evidence
   validation. Supply the protected snapshot, completed summary/hash, reviewed
   contract/hash and supervisor receipt reference. It reads the canonical live
   ledger **read-only**. Both databases must have the same activation cookie;
   record ID, original source identity/payload and immutable capture audit fields
   must agree. Its draft binds every input hash and the activation marker hash.
2. `approve_request(inbox, request_id, draft_sha256, ids, approval_reference=...)`
   approves at most **10** selected records. Held records additionally require the
   exact `held_ids` and a separate `hold_reference`. Approval never submits work.
3. `dispatch_request(inbox, request_id, approved_sha256, execution_reference=...)`
   publishes the separately initiated request. Existing filenames cannot be replaced.
4. `cancel_request(inbox, request_id, cancellation_reference=...)` publishes a
   cancellation bound to that request. It prevents further requests; it cannot undo
   an in-flight remote commit. Such outcomes require independent verification.

No new database, credentials, sender process or service is created. The handoff is
private local JSON; **Windows NTFS ACLs authenticate allowed writers**, rather than
an untrusted browser username or a shared API key. `Inbox` reuses Phase 1's strict
`private_storage` check (owners, allowed principals, fixed NTFS storage, reparse and
cloud restrictions). SYSTEM and Administrators are trusted. For a non-default
operator/service account, `trusted_sid` is the approved service account SID on the
manager side and the approved operator SID on the service side. These are identities,
not new credentials. Actual account/ACL access still requires Windows verification;
mocked ACL tests do not prove it. Do not grant the manager write access to the ledger.
This is same-PC trusted-principal authentication, not a signature/nonrepudiation system.

The inbox must be separate from the live ledger, snapshot, reports and production
directories and pass existing protected-output checks. Inputs and inbox remain
private. Audit bodies contain selected identifiers and review references; keep them
private. Raw records, exceptions, credentials and HTTP bodies are not logged.

## Existing service writer

The small hook in `durable_sync` runs inside the existing global writer ownership.
It is inert by default. `HISTORICAL_RECOVERY_COORDINATION=True` only enables calling
the hook; **it cannot enable production transport**. `service_tick` requires the
exact existing file-only `SimulationTarget`, which the real service never supplies.
Synthetic tests also supply `HISTORICAL_RECOVERY_INBOX`,
`HISTORICAL_REQUEST_AUTHOR_SID` and an independently pinned
`HISTORICAL_RECOVERY_CONTRACT_SHA256`. Do not set these in production for this stage.

Optional `sr_requests`, `sr_items`, `sr_intents` and `sr_events` tables live in the
existing ledger; no production schema migration is performed. Schema definitions
are checked before use. `request_results` reads the private durable audit.

For each selected item, the service repeats the complete, paginated Phase 2A
employee/destination comparison using service-owned credentials. It rechecks current
canonical state, delivery history and peers, then atomically commits a unique
destination/source intent and delivery lease **before POST**. The normal-delivery
transport, pacing, backoff, Retry-After and destination cooldown are reused. Normal
claims cannot bypass recovery intents. Overlapping normal sources require verified
mapping and destination identity; unrelated normal attendance retains its delivery
rules. Without the verified overlap guard, those historical seconds fail closed.

HTTP 200 and duplicate wording are provisional. Only independent destination proof
can produce `Recovered`/`Already Exists`. Crashes/timeouts burn the intent's POST
permission: subsequent processing only verifies, never blindly resubmits. A failed
preflight also cannot automatically turn Needs Review back into permission to send.
The cutover boundary remains unchanged. A held item may transition only with its
separate selected-record authorization; uncertain outcomes restore its hold.
Unselected holds and existing capture/retry policies are unchanged.

Each dispatch has **120 seconds from dispatch**, at most **128 total HTTP attempts**
persisted across restarts, at most **10 selected items**, and HTTP timeouts capped at
**3 seconds connect / 10 seconds read inactivity** (each capped by the remaining
deadline). Redirects are denied at the budget boundary. Approval expires after 24 hours. One request
is processed per existing service tick. Cancellation/deadlines are checked at pacing,
HTTP and publication boundaries; late responses cannot yield a success receipt.
The deadline is cooperative for an in-flight Phase 1 transport call: socket inactivity
timeouts are not a hard end-to-end deadline against a continuously trickling server.
The selected minimal controlled-test policy accepts this finite-request behavior
with explicit operational acknowledgement that an in-flight call can delay normal
delivery. It does not require or claim a hard wall-clock bound; no second sending
process or automatic retry is introduced. Exhausted/cancelled requests remain Needs Review.

The archived Phase 1 staging harness keeps its original source pins and will
correctly reject these changed service modules. Development regression tests use
an explicit three-file Stage 2 hash overlay; this does not extend Phase 1 release
certification to Stage 2.

## Validation and remaining gates

Run the synthetic focused suite:

```powershell
$env:PYTHONPATH='tests'
.\.venv\Scripts\python.exe -B -m unittest test_service_recovery -q
```

Final sequential relevant regression run: **523 tests passed in 145.552 seconds**,
including **34 Stage 2 tests** plus the existing 489 manager/Phase 1/Phase 2 cases.
Coverage includes real local writer-mutex exclusion, canonical and peer claim
races, duplicate/collation-equivalent identities, changed inputs/state, approval
separation, scoped holds, durable intent before POST, crash-before/after remote
commit, timeout/read-only reconciliation, false HTTP success/duplicate messages,
mapping changes, private-storage failure, durable request limits, cancellation,
late responses, cross-lane pacing and read-only audit access. Earlier overlapping
test commands contended on the global mutex; the reported final run was sequential.
This is local synthetic/mocked validation, not production certification.

Before any separately authorized production enablement: verify effective Frappe
GET/POST handlers/hooks, database employee equality/visibility, destination duplicate
uniqueness and stability, timezone/precision/historical payload policy, snapshot/live
activation binding, real service/operator ACLs and token ownership, service-held mutex
and restart behavior, transport deadline/cancellation behavior, scoped destination
quiescence and the specific cutover exceptions. Obtain a reviewed production gate
change, an execution window and operational approval. Unknown claims remain false.
Do not reset queue states, delete intents, release holds globally or stop the service
as a workaround. No live verification or production acceptance is claimed here.
