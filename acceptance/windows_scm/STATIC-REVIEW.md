# Safety review of the repaired acceptance package

Application baseline: 4f20179f5fb10f84a6f86a6ff720f9c2876efc20.
Scope: acceptance/windows_scm only. No synchronization, recovery or monitoring
policy source changes. This is preparation evidence, not SCM acceptance.

## Defects and corrections

| Issue | Root cause | Correction |
|---|---|---|
| Active processes missed | Cleanup compared against two backslashes and only inspected non-null ExecutablePath | Normalize slash/case/extended paths; check path boundaries, executable and command arguments; classify missing/ambiguous metadata as unknown; require no active/unknown process before deletion |
| Evidence dead end | Final directory was created before collection/hash completion; retries refused it while cleanup required a missing manifest | Unique protected UUID staging; required-file/exact-hash validation; same-volume atomic rename without replacement; preserve and archive interrupted attempts; cleanup verifies their preservation |
| Mutex takeover race | One-time absence observation did not reserve ownership; contender could acquire when expected service exited | Create-only Operator and Worker leases; atomic initial ownership of unchanged global mutex; original wrapper uses recursive same-thread locks; read-only owner PID/TID query with SCM/process-generation checks; quiet leases protect evidence/cleanup |
| Reboot left Automatic | Verification could throw before the restoration command | Separate optional stages; verification in try/finally; checked Manual restoration; explicit RestoreManual stage and independently guarded manual recovery commands; no reboot command |

## Static review of every privileged action / mutation family

| Action | Guard / target / failure behavior |
|---|---|
| Administrative stage dispatch | Actual elevated-admin token plus exact approval phrase; create-only unique Operator mutex; release in finally. Direct Preflight creates no mutex |
| Native child execution | Absolute executable, shell disabled, Windows argument quoting, trusted base runtime before execution, no-profile operator instructions. Timeout kills only the newly created child held by its Process handle |
| Runtime / source reads | Reparse check before traversal, protected owners and write masks for base runtime/ancestors, fixed application Git archive, reviewed host/driver/helper copy hashes |
| File creation / venv / offline pip | Only new fixed Root; preflight rejects collisions. Approved wheel-manifest digest and per-wheel hashes, protected copies, binary-only/no-index install, frozen versions and pip check |
| Set-Acl / ownership | Only newly created fixed Root/Evidence descendants; Owner-Only called from validated Root tree. No base-runtime, production, account-rights or machine ACL repair |
| TLS key/certificate | Fixed Root/tools; dummy loopback certificate only; no trust-store import |
| Provision / SQLite writes | Fresh fixed synthetic DB/activation/config; exclusive-worker guard; real unchanged private-storage validator. Recovery flags false; no production path arguments |
| Service creation | Fixed unique name/display, fixed protected executable/ImagePath, default LocalSystem, Manual. Exact account/path/display/start mode verified afterward |
| Service start/restart | Only recognized stopped service, repeated process/mutex checks. Atomic SCM-thread worker claim precedes receiver and application writes |
| Service stop | Only exact recognized service/account/ImagePath; wait for Stopped. No worker process termination or force-stop of another service |
| Mutex operations | No waiting/adoption. Existing object/access denial fails. Contender only opens QUERY_STATE and queries expected PID/TID; never acquires. Quiet stages create-only claim both worker/global objects |
| Evidence copy | Root tree checked before recursion; private staging and trusted ownership; consistent SQLite backup from mode=ro, integrity check and activation copy |
| Evidence publication | Completeness, identity, exact file hashes and prior-attempt archives validated; Windows rename on same C: volume; existing final path refused; no overwrite/copy fallback |
| Evidence retries | New UUID, old bytes preserved. Empty/partial initial marker recovery limited to protected, receipt-owned staging and canonical UUID with no unexpected payload. Invalid identity or ownership refuses |
| Service deletion | Fixed sc.exe delete after recognized service stop, evidence verification and complete root inventory check; verify removal, pending SCM deletion refuses file removal |
| Root deletion | Exact literal fixed path; source/config/receipt/ownership, no reparse, complete evidence and archived partials, inventory/known-output checks; quiet leases held; process check immediately before one narrowly scoped recursive removal |
| Optional startup changes | Separate explicit reboot approval for Automatic; recognized identity; snapshot recorded; finally restores and verifies Manual; independent manual recovery documented |
| Outside disposable environment | Wheelhouse and Python read only during validation; repository read/archive only during later operator stages; no production devices/endpoints/credentials, ACL changes or deployment |

The base runtime and wheelhouse were NOT installed/provisioned by the preparer.
Only ordinary-token tests ran. ENVIRONMENT.txt records the actual token and accepted
development environment. The full application suite uses its existing isolated fixtures.

## Limits and execution recommendation

NO-GO for elevated execution now: the default protected Python installation and
wheelhouse were absent at verification; no monitoring SID/logon, readable elevated
inventory or exclusive host-use window has been approved/proved here.

Conditional GO for the documented isolated stages only after all prerequisites
and read-only preflight pass, package checksums are independently reviewed and the
human explicitly authorizes elevated execution.

The harness cannot prevent unrelated privileged operators/schedulers from starting
external workers between stages or after crashes. Holding the production-name mutex
can make such a worker fail startup. A genuinely exclusive host-use window is
mandatory; do not stop/alter another service to manufacture it. An unclassifiable
protected process is a blocker, not grounds for a permission exception.

NtQueryMutant owner information passed a real ordinary-token Windows test using a
unique Local unit mutex. Global object access under LocalSystem, actual SCM start/
stop, monitoring identity/ACL/S4U behavior and optional reboot remain NOT TESTED.
Unsupported owner queries fail closed. Clipboard monitoring JSON needs operator
attestation. Malicious privileged changes and filesystem/power failures during
cleanup require review; no broad forced recovery is authorized.
