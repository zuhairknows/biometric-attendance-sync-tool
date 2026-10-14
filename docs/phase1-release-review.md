# Phase 1 final review and release preparation — October 14, 2026

**Outcome A: Development verified, ready for controlled staging.** No claim of
production readiness. No production deployment, service operations, device calls,
Frappe records/config changes, dependency installation or Phase 2 work was performed.

## Git verification

Starting branch: `feature/phase1-durable-sync`, clean tree, expected HEAD verified
as **43e5ac47e7b9832e5e182e5cf43eeadf8c9acc13**. The entire original Phase 1 diff
against **a23b8112fd1b87f3d2bb10f798b45e150aadcd7a** was reviewed with current source,
both guides, existing/new tests, configuration examples, schema and migration code.
The baseline-name discrepancy remains documented in the architecture guide; no
stable branch or main history was substituted/changed by this review.

Correction commit: **e00dd27**. HTTP/inspection test tooling commit and tested code
candidate: **222440465ca35df7f811642984e37ba5bd905d78**. The release-preparation
commit after that adds documentation only. Get its full SHA from the final report
or `git rev-parse feature/phase1-durable-sync`. Publication is only this development
branch, a normal non-force push after clean-tree/tests checks. No merge/tag/release
or production deployment is authorized. Branch:
[feature/phase1-durable-sync](https://github.com/zuhairknows/biometric-attendance-sync-tool/tree/feature/phase1-durable-sync).

## Confirmed defects and corrections

| Finding | Correction and evidence |
| --- | --- |
| Version number alone accepted a missing/changed table/index; an absent destination row could break delivery | Compare version-1 table/index SQL contracts with an in-memory reference, preserving literal case; validate singleton. Missing table/index, changed CHECK, altered columns, missing destination and corrupt-file tests fail closed without rewriting the source |
| `int(1.9)` silently truncated source UID/punch/status and could merge different records | Strict nonnegative integer or digit-string validation; floats/booleans quarantined; full invalid evidence is hashed before display truncation |
| `strptime` accepted short YYYYMMDD inputs, potentially widening eligible history | Require exactly eight ASCII digits plus calendar validation; malformed date fails closed |
| `'False'` was truthy and could unintentionally enable durable mode | Require an actual Python boolean before any synchronization |
| Windows wrapper never propagated stop into durable work; long batches/rate waits could keep processing after stop | Optional `main(stop_requested=...)`, stop checks before claims/devices/shifts, 0.5-second rate-wait slices, existing Win32 stop-event wait instead of fixed sleep. Current calls/transactions finish safely |
| Legacy PickleDB initialization/save could block SQLite pending recovery if old status was damaged/unwritable | Durable polling uses persisted SQLite scan attempts. Durable startup uses a read-only legacy JSON floor snapshot, preserves its bytes, resumes delivery with corrupt status, and blocks Shift Type writes until that status is audited |
| Local config copies could enter ordinary staging accidentally | Ignore local config Python/backup variants while explicitly retaining the template. Current tracked config contains placeholders only; force-add can bypass Git ignores and is never part of this procedure |

## Reliability assessment

Punches and successful-fetch metadata commit together before eligibility for HTTP.
Claims increment persistent attempt count and establish a lease transactionally;
completion and bounded attempt history commit together. Crashed claims recover only
after expiry, and lost remote creation responses resolve through the specific
duplicate response. No automatic record deletion or retry-count discard exists.
Due queries are indexed and bounded; new/old pending and retry fairness remains.
All mapped devices require a successful latest scan; the watermark is capped before
**every** unresolved eligible record, not just retry rows. Unknown timestamps block
it entirely. Existing already overadvanced remote watermarks need operator audit.

HTTP timeout/network/408/429/5xx keep rows retryable with capped exponential jitter;
valid Retry-After may extend delays. Authentication/unknown responses persist a
destination cooldown instead of producing record-by-record bursts. Known missing
and inactive employees remain quarantined and block watermark advancement; unrelated
417 never becomes success. Payload fields/version selection and permanent device IDs
are preserved. Model UID stability and actual response translation remain staging gates.

The connector still returns sequential full device history; malformed decoded
records are independently quarantined. Raw pyzk decode failure before returning
records remains a failed scan rather than an unproven repair. Legacy dumps are
explicit preview/apply only, transactionally idempotent per file, hash-audited and
never removed. No destructive schema migration is introduced. Schema stays 1;
altered/unsupported schemas are not rebuilt or downgraded.

## Tests executed and not executed

* Initial review baseline: **74/74 passed**, 26.402 seconds.
* Final suite: **92/92 passed**, 16.607 seconds, using `py -m unittest discover -s tests -q`.
  Includes original 27 unchanged regression tests, 47 initial Phase 1 tests and
  18 release-review/HTTP tests. Temporary SQLite and mocked external/device APIs only.
* Standalone `py scripts\phase1_http_harness.py`: **10/10 scenarios passed**, 9 fake
  Checkins, actual HTTP sockets on ephemeral **127.0.0.1** only; no proxy/redirect or
  configurable external host. The same harness is part of the full suite.
* Real child-process exits before punch commit and after commit, interrupted SQLite
  transactions, disk-write abort injection, corrupt schemas/files, remote creation
  before local confirmation, restart, multiple unresolved mapped-device punches,
  repeated engine/history cycles, timeout/outage/auth and dump failures exercised.
* **19 Python files** syntax-compiled without production config imports or new pyc
  artifacts. `git diff --check` passed. PowerShell helper parsed only; embedded Python
  inspector executed against fake configs to prove no config execution or credential
  echo on syntax error. Full production-PC helper: **NOT EXECUTED**.
* Actual ZKTeco retrieval/firmware layouts/UID behavior: **NOT EXECUTED**.
* Real nonproduction Frappe API/Auto Attendance: **NOT EXECUTED**.
* Windows SCM/service-account ACL/start/stop/restart: **NOT EXECUTED**. Wrapper
  behavior is mocked; that does not establish actual SCM compatibility.

The first added schema test reused a temporary filename in two subcases; that
fixture isolation error was corrected. The fake timeout fixture initially printed
an expected Windows socket-abort traceback; it now handles that expected condition.
Neither error involved production or loss of ledger records.

## Files changed in this release review

| Files | Change |
| --- | --- |
| `.gitignore` | Exclude credential-bearing config copies; preserve tracked template |
| `sync_ledger.py` | Structural schema checks, read-only integrity command backend, durable polling metadata |
| `punch_normalization.py` | Strict source integer/user/date validation and collision-safe invalid identity |
| `durable_delivery.py` | Cooperative stop during rate waits and before claims |
| `durable_sync.py` | Legacy read-only snapshot, SQLite polling, stop-aware Shift Type loop |
| `erpnext_sync.py` | Boolean activation, optional stop callback, durable startup isolation |
| `erpnext_sync_win.py` | Callback propagation and existing stop-event wait; same hosting/name |
| `sync_cli.py` | Read-only `verify` command |
| `tests/test_durable_sync.py` | Update restart and wrapper integration fixtures |
| `tests/test_phase1_release.py` (new) | Confirmed-defect, crash/schema/source/stop/status/config-inspector regression coverage |
| `scripts/phase1_http_harness.py`, `tests/test_phase1_http.py` (new) | Isolated repeatable real HTTP fixture scenarios and external-URL refusal |
| `scripts/verify_phase1_pc.ps1` (new) | Prepared read-only production-PC inventory, safe operational config inspection |
| `README.md`, `docs/phase1-durable-sync.md`, `docs/phase1-verification.md` | Updated entry points, runtime semantics and current verification |
| `docs/phase1-integration-tests.md`, `docs/phase1-cutover-runbook.md`, this report (new) | Live staging procedure, exact future Windows commands, gates, rollback/forward recovery |

Other original Phase 1 modules/config templates/tests were reviewed but remain
unchanged by release corrections. Actual `local_config.py`, `SMWinservice.py`,
requirements, installer, main/stable branches and production resources are untouched.

## Deployment isolation and recovery

Runtime rejects a relative/inside-checkout database; tests/harness use separate
temporary files. Mode defaults False. Ledger/attempt errors store safe fixed messages,
no tokens/passwords/headers. CLI status/verify/backup are read-only on the source;
backup handles close and restored copies validate. Dumps/status/logs remain evidence.
Git ignore is defense against accidental staging, not protection against an explicit
force-add or secrets manually pasted into source; publication stages reviewed paths
only and the PC helper confirms local config is not tracked.

[Cutover runbook](phase1-cutover-runbook.md) covers inventory, ACLs, stopped-worker
backup, source pinning, migration, activation, startup, per-device backlog, manual
Frappe interval reconciliation and watermark checks with measurable GO/NO-GO gates.
All production commands are prepared only. [Integration procedure](phase1-integration-tests.md)
must be completed on isolated staging first.

Preferred recovery preserves the **latest** post-cutover ledger and resumes with
durable-compatible code. A simple rollback to the old engine cannot account for
new SQLite-only captured punches. Stop, backup and reconcile the complete interval;
never overwrite that ledger with a pre-cutover snapshot. Legacy rollback requires
zero unresolved states or a separately approved complete recovery procedure.
If a recovery ledger is necessary, restore to a new file and reconstruct/reconcile
the post-backup interval from retained source evidence before switching the worker.

Remaining production-PC requirements are live model/decoder/UID proof, real duplicate
formats, actual service account/NTFS paths/ACLs, SCM timing, representative history
load/capacity, source-dump completeness, remote Checkin reconciliation and existing
watermark audit. Until those pass and deployment is separately approved, production
is NO-GO. No unresolved material local reliability defect remains from this review;
the listed integration risks are controlled-staging gates, not claimed passes.
