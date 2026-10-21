# Historical recovery development and acceptance

Date: 21 October 2026 (Asia/Riyadh). Starting upstream:
`origin/recovery` at `29a440ced130d0f9424d914ae6761b8b54d93a62`.
This report covers development completion, not a production deployment.

## Architecture and feature status

```text
12 ZKTeco devices → existing sequential service polling → canonical SQLite ledger
                                                           ↓
                                                 normal durable delivery
                                                           ↓
                                                   ERPNext Checkins

approved SQLite backup + reviewed comparison contract + complete destination GETs
                         ↓
manager investigation → private CSV/JSON → exact selected IDs → approval → dispatch
                                                                         ↓
                                                              private file inbox
                                                                         ↓
existing service writer + pinned release evidence → intent before POST → verification
                                                                         ↓
                                                   durable result/audit → manager report
```

There is one service writer, one canonical ledger and the existing manager.
No new application, sending thread or helper service was added. The TLS receiver
and browser HTTP server used in acceptance are disposable test fixtures only.

| Area | Implemented behavior | Evidence boundary |
| --- | --- | --- |
| Continuous synchronization | Existing polling, normalization, deduplication, durable retries, cutover/holds, checkpoints and shutdown ownership retained | Synthetic 12-device cycle and existing engine/crash tests; physical devices unverified |
| Historical capture | Normal service capture retains available full history in the ledger, including held evidence | Does not reconstruct erased or overwritten device logs; manager does not fetch devices |
| Investigation | Inclusive start/end; device selection; full count/page/repeat comparison; employee database equality; original timestamp/device identity; invalid, duplicate, unmatched, ambiguous, unverifiable, already existing and potentially missing outcomes; separate hold flag | Missing status requires every independent comparison claim; restricted/partial GETs never prove absence |
| Production investigation | Existing acknowledgement and compatibility-only behavior retained unless a current pinned release separately authorizes comparison | Release does not fill unknown claims or authorize submission; exact deployed source/permissions still require review |
| Preview and review | Offline preview; independently reviewed complete summary; canonical immutable identity check; at most ten selected records; separate selected-hold exception | Preview alone is never recovery evidence or authorization |
| Approval and dispatch | Separate immutable files, expiry, explicit references, bounded selection, cancellation; repeated actions reuse existing publication | OS ACLs authenticate the handoff; launcher capability protects normal recovery browser sessions |
| Service execution | Actual service cycle can construct the reviewed transport only with explicit enablement and pinned release evidence | Ships disabled; no production release authorization created here |
| Delivery safety | Persistent unique source/destination intent before POST, existing writer mutex and normal-overlap fencing, no blind replay after uncertainty/crash, independent destination proof | Real duplicate atomicity/quiescence and destination semantics are production prerequisites |
| Bounded I/O | Numeric address avoids DNS; nonblocking verified TLS; three-second TCP connect and at most ten-second absolute request deadline; 100 ms cancellation checks while waiting; body size cap; no proxy/redirect/retry | Not a hard real-time bound for file hashing, ACL subprocesses, SQLite, OS scheduling or the whole normal service tick |
| SQLite compatibility | Optional additive `sr_*` tables, atomic installation, unchanged schema version 1, unchanged global holds/checkpoints | Older unfenced senders are unsafe with unresolved intents; rollback must retain fencing |
| Manager | Minimal existing pages; read-only canonical access; private diagnostics; one-use normal-launcher authentication; record status; private CSV/JSON final report | Monitoring-only permissions/routes/startup stay independent of recovery |
| Installation/release | Explicit service requirements, venv-aware native staging package, clean-source ZIP builder, disabled template, activation/backup/rollback/reboot runbook | Actual SCM install/update, service-token ACLs and reboot not performed |

The public investigation category `potentially_missing` means absence under the
reviewed complete comparison, not permission to send. Queue state and historical
hold are separate fields. The final service statuses Recovered and Already Exists
require independent destination identity confirmation; HTTP 200 alone is insufficient.

## Implemented changes and commits

- `8bf5fc0`: reconcile archived staging tests with their exact reviewed Phase 1
  revision, preserve frozen hashes including original line endings, isolate native
  PowerShell 5.1 module lookup and package pywin32 from the invoking venv.
- `21d99cf5f433632846a9f0f2eb8af0f64d85a62a`: deployable release-gated service
  recovery, bounded HTTPS transport, release-aware read-only comparison,
  authenticated manager handoff, cancellation after release revocation, private
  service result exports, explicit service dependencies and new acceptance coverage.
- The subsequent release-preparation commit adds this report, operator/deployment
  instructions, disabled release template and clean-source package builder. Its
  exact SHA is recorded by `git log` and the package's `release-info.json`.

No merge into `main` or production activation is part of these changes. The local
branch is `recovery`. Remote publication is a separate action.

## Previous 738-test report: disposition

The prior reported result was 738 tests, seven failures and one setup error.
It was not evidence of a wholly passing release.

| Previous issue | Classification | Resolution |
| --- | --- | --- |
| Four archived engine/hash/crash/timeout failures | Obsolete staging-source assumption | Materialize and hash-check the exact immutable `4256cd6` sources from local Git objects; run the frozen harness against those sources. Current runtime keeps separate Phase 2 pins and behavioral tests. |
| Three missing `Get-FileHash` failures | Test-host environment dependency | The bundled PowerShell 7 host exported its module path to Windows PowerShell 5.1. Tests now use native WinPS module paths for child processes and restore the process environment afterward. No machine environment or hashing assertions changed. |
| SCM fixture setup could not find pywin32 | Packaging defect | Standard library comes from base Python, while native pywin32 comes from the selected venv, including Python 3.11 `-S` invocation handling. Missing dependencies still fail closed. |

The reconciled targeted staging run passed **39 tests in 90.616 seconds**.
No meaningful assertion was disabled and no production gate was relaxed to make
the historical tests pass. Frozen Phase 1 evidence certifies that archived code,
not the new recovery service.

The initial sandbox attempt was unusable as acceptance: Windows denied resolving
disposable temporary paths, producing 163 failures and 142 errors over 617 tests.
An intermediate unrestricted run, during implementation, reported 738 tests with
nine failures and one setup error; it included the then-unfixed staging issues,
a deliberately changed source pin and the now-updated cancellation assertion.
These diagnostic runs are superseded by final serial acceptance, not hidden as passes.
Test processes using the global writer mutex must run serially.

## Final development validation

The full serial development run passed **755 tests in 375.006 seconds**, with
zero failures, errors or skips. Raw local evidence is retained in
`.acceptance/full-suite.txt`. The complete suite includes the preserved Phase 1,
investigation, preview, recovery, HTTP manager, monitoring, native mutex,
crash/restart, locking, permissions and packaging regressions.

A second fresh Git clone at `21d99cf` and a newly created Python 3.11 x64 venv,
installed with only `requirements-service.txt` and `requirements-manager.txt`,
passed **all 755 tests again in 377.318 seconds**, with zero failures, errors or
skips. `pip check` reported no broken requirements. The exact package versions
are frozen in `docs/requirements-windows311-accepted.txt`. Raw fresh-clone output
is retained in `docs/validation/historical-recovery-full-suite.txt`; local install
and intermediate diagnostic evidence remain under ignored `.acceptance/`.

The new release acceptance module adds **12 tests**, including these real paths:

- A normal manager served over actual loopback HTTP denies an unauthenticated
  recovery browser, consumes its private launcher token once, checks CSRF and
  completes prepare → selected approval → separate dispatch with **zero** remote
  requests from those operator actions.
- The existing `durable_sync.run` polls all 12 synthetic device identities with
  deletion disabled. Its actual service hook constructs the gated transport;
  loopback TLS performs preflight comparison, one selected recovery POST,
  independent confirmation and ordinary synchronization. Fifteen total POSTs are
  expected: one selected recovery, twelve newly polled normal punches and two
  pre-existing unselected normal punches. A second cycle sends no historical replay.
- The canonical ledger retains one recovery intent and twelve device states; the
  HTTP status page shows Recovered and private CSV/JSON export confirms one recovery.
- Revoking release evidence blocks recovery while a newly ingested unrelated
  normal punch still delivers. Existing intents remain. Operator cancellation is
  accepted even after release revocation.
- Real verified TLS tests interrupt a continuously trickling response at its
  absolute deadline, cancel an in-flight read, reject an untrusted certificate,
  reject disabled certificate verification and prove no DNS call or transport retry.
- Release tests reject wrong site/contract/activation/ledger/code, missing evidence,
  bad address, altered hash, failed storage access, expiry/future/nonfinite/overlong
  approval. Approved production-labelled synthetic comparison uses GET only;
  unknown visibility still blocks missing classification without issuing HTTP.

Existing tests cover uncertain results before/after remote commit, process death,
durable intent fences across restart, cancellation/expiry, global mutex contention,
selected held records and unselected hold preservation, incomplete destination
counts, source drift, restricted startup modes and SQLite locking/access failures.

All inputs were synthetic and all network receivers were literal loopback. No
production config, ledger, device, Frappe endpoint or IT-SUPPORT service was used.
Actual browser-engine visual QA is not part of these HTTP acceptance tests.

## Unresolved production-only prerequisites and release decisions

| Scope | Decision | Reason / prerequisite |
| --- | --- | --- |
| Existing monitoring deployment | Preserve | User reports it working; this mission did not inspect or alter IT-SUPPORT |
| Updated monitoring-only manager | CONDITIONAL GO | Development HTTP/SQLite/restart checks pass; verify the existing real profile/account and login/reboot acceptance before replacement |
| Updated production synchronization service | NO-GO for production replacement yet | Actual SCM installation/account/token/ACL/reboot, loaded code and all physical device connectivity/polling must be accepted on the target deployment |
| Historical investigation | CONDITIONAL GO | Local/synthetic behavior passes; production GET safety, complete visibility, reviewed timezone/mapping/duplicate semantics and scoped read approval are still required |
| Live historical recovery | NO-GO | No real release authorization; deployed handlers and historical policy, visibility, duplicate atomicity/quiescence, actual service/operator access and restart/rollback evidence remain unverified |

The code and synthetic acceptance do not establish production semantics. Evidence
references in a release file are administrator attestations backed by external
review, not cryptographic proof of those facts. Do not populate unknown claims or
enable recovery merely because the automated suite passes.

Follow [deployment, operation and rollback](historical-recovery-deployment.md).
Keep the preserved monitoring/service paths, narrow read grant and private profile
until their separately approved production replacement. No production readiness
claim or permission to send experimental Checkins is implied by this report.

## Subsequent monitoring ACL development candidate

The 755-test and fresh-clone results above describe the preserved baseline.
The optional LocalSystem monitoring-reader compatibility fix has separate
[policy and acceptance requirements](monitoring-acl-compatibility.md) and
[development validation evidence](validation/monitoring-acl-compatibility.txt).
It does not authorize production replacement or historical recovery.
