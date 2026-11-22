# Phase 2B controlled historical recovery

This implements finite recovery batches, explicit selected-record approval,
pre-send destination revalidation, Phase 1 sending, durable submission intent and
independent post-send verification. There is no second service or automatic replay.
**Production execution is disabled. The only executable destination is a persistent
synthetic fixture with a `.example.test` origin. No live transport is provided.**

## Safety and statuses

Preparation requires externally reviewed SHA256 hashes of a complete Phase 2A
summary and its unchanged contract, the matching CSV/hash, and the exact protected
self-contained rollback-journal SQLite backup. It verifies the Phase 2A selected
snapshot fingerprint and all original source/audit CSV columns. The operator must
reference their review of the completed supervisor receipt. A hash/reference binds
reviewed artifacts; it does not establish deployed permission/hook semantics.

Production compatibility-only/unverified reports are rejected. Every original
Phase 2A verification claim must be true. Recovery supports only the reviewed Phase 1
employee/time/log-type identity at whole-second precision. It preserves NULL/blank
rules through the existing Phase 2A comparator. Ambiguous, duplicate, unmatched,
invalid or uncertain evidence cannot become a candidate.

| Status | Meaning |
| --- | --- |
| Pending Approval | Verified missing source awaiting explicit selection |
| Approved | Operator selected this original record ID and pinned batch fingerprint |
| Already Exists | Independently verified matching destination identity; no further POST |
| Recovered | Phase 1 returned a document name and fresh complete reads confirmed that exact identity/name |
| Skipped | Original state/attempt/hold disallows recovery; never reset |
| Needs Review | Mapping/API uncertainty, prior submission intent, interruption or unconfirmed response |
| Failed | Phase 1 business rejection not independently confirmed as existing |

Only untouched `pending` source rows with attempt count zero, no error category and
no Checkin name can be approved. All other potentially missing rows are recorded as
Skipped. Cutover holds remain unchanged, including pending rows carrying a hold.
There is no command/API to release holds, reset failed items or clear submission intent.

## Concurrency, crash safety and delivery reuse

Use one canonical private recovery workspace for a destination; never replace,
delete, fork or recreate its audit store to bypass a prior intent. All batches share
its identity registry. `recovery-state.recovery-audit` uses SQLite FULL durability,
transactional status/event commits and a recovery-command file lock. Source SQLite
is always opened read-only. Approval/execution verify the immutable batch fingerprint
and every pinned input hash again; changed inputs require a newly reviewed investigation.

Execution acquires Phase 1's existing worker lock before reads/submissions. On Windows
this is the same global service mutex. If the normal service owns it, recovery refuses
without stopping it or changing state. Portable tests bind to the same synthetic
normal-worker path. This proves same-host exclusion only; it is not cross-host fencing.

Each approved row gets a fresh complete Phase 2A comparison with authoritative
employee equality lookup, count/pagination checks and repeated reads. Mapping must
still equal the investigated employee. Phase 1 `Ledger.has_uncertain_peer` also blocks
known uncertain peers, including unselected devices. Existing destination identities
are skipped. Otherwise a unique destination-identity intent and Needs Review event
commit BEFORE calling the existing `durable_delivery.FrappeClient.send` path.
The source device, punch identity and original saved payload are preserved.

There is at most one POST intent per destination identity in this workspace. Even a
crash immediately before sending burns permission to send that identity again.
After any uncertain outcome, explicit repeated execution performs reads only:
positive proof becomes Already Exists; absence remains Needs Review. No empty read
permits retrying an uncertain POST. HTTP 200 alone never becomes Recovered.
This deliberately favors duplicate prevention over automatic recovery throughput.

## Synthetic CLI workflow on a development PC

Create existing private sibling directories outside checkout/production/input folders
for `audit` and `results`. Keep actual Windows ACL checks; no production credentials
or `local_config.py` are needed. The simulation JSON changes to model remote commits;
the source backup, Phase 2A artifacts and contract do not change.

```powershell
# Creates NEW synthetic inputs only; refuses an existing destination directory.
py -B tests/generate_recovery_example.py 'C:\Phase2BReview\inputs'
$Workspace = 'C:\Phase2BReview\audit'
$Inputs = 'C:\Phase2BReview\inputs'
$SummaryHash = (Get-FileHash "$Inputs\synthetic-summary.json" -Algorithm SHA256).Hash
$ContractHash = (Get-FileHash "$Inputs\synthetic-contract.json" -Algorithm SHA256).Hash

py -B scripts/recover_attendance.py --workspace $Workspace prepare `
  --ledger-copy "$Inputs\synthetic-ledger.sqlite3" `
  --summary "$Inputs\synthetic-summary.json" --summary-sha256 $SummaryHash `
  --contract "$Inputs\synthetic-contract.json" --contract-sha256 $ContractHash `
  --receipt-review 'Synthetic complete investigation reviewed'

# Privately inspect the inputs/results. Copy the printed batch/fingerprint exactly.
$Batch = 'REPLACE_WITH_BATCH_ID'
$Fingerprint = 'REPLACE_WITH_BATCH_FINGERPRINT'
py -B scripts/recover_attendance.py --workspace $Workspace approve `
  --batch $Batch --fingerprint $Fingerprint --record-id 1 `
  --approval-reference 'Explicit synthetic approval for record 1'
py -B scripts/recover_attendance.py --workspace $Workspace execute-simulation `
  --batch $Batch --fingerprint $Fingerprint `
  --simulation "$Inputs\synthetic-destination.json" `
  --normal-worker-path 'C:\Phase2BReview\synthetic-normal.sqlite3'
py -B scripts/recover_attendance.py --workspace $Workspace show --batch $Batch
py -B scripts/recover_attendance.py --workspace $Workspace export `
  --batch $Batch --output 'C:\Phase2BReview\results'
```

CLI output contains batch IDs/fingerprints/counts, not employee identifiers or remote
exception details. CSV/JSON exports contain private source evidence and audit events;
retain them in protected storage. JSON includes the CSV SHA256 and submission outcomes.
Writes never overwrite existing exports. `show` gives safe counts; export also permits
private review before approval. Exit codes: 0 command completed, 2 execution still has
Approved/Needs Review/Failed items, 1 blocked/error, 130 interrupted. Pending Approval
and Skipped rows never submit. A Completed command is not production authorization.

## Verification and genuine production blockers

The later [Stage 2 service-owned backend](phase2-stage2-service-recovery.md) adds a
synthetic-only service handoff and selected-hold authorization design. It does not
enable this CLI's live transport or authorize production execution. The limitations
below describe the original Phase 2B CLI.

Run the focused suite with `PYTHONPATH=tests`:
`py -B -m unittest test_historical_recovery -v`.
It uses synthetic SQLite, mocked ERPNext and a local process holding the Phase 1 mutex.
No live site, device, deployed service or production ledger is accessed.

Production remains NO-GO. Independent endpoint/permission/hook/duplicate/precision
verification from Phase 2A is still incomplete. Compatibility-only production results
cannot seed batches. Historical held records need a separately authorized, designed
and verified transition; none exists here. The currently running service holds the
required writer mutex, so simultaneous same-host recovery is blocked, not silently
made concurrent. Cross-host fencing, one authoritative audit workspace, source-to-live
ledger ownership, deployment freshness and an approved execution window must be
established before any future live enablement. Server-side identity uniqueness and
stability of mappings between revalidation and submission must also be verified.
Do not stop the service, change permissions/holds, clear intents or enable transport
as a workaround. No Phase 2C work is included.
