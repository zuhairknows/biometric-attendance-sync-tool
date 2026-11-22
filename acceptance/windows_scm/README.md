# Phase 2 isolated Windows SCM acceptance

PREPARATION ONLY. Administrative execution needs separate human authorization.
Application candidate: 4f20179f5fb10f84a6f86a6ff720f9c2876efc20 on recovery.
The harness is committed separately; Prepare always exports that exact application
commit. Preflight accepts subsequent commits only when their entire candidate-to-HEAD
diff is inside acceptance/windows_scm/. No application policy is changed.

## Fixed isolation

| Resource | Only permitted target |
|---|---|
| Machine | ZUHAIR-PC |
| Service | FPFPhase2AclAcceptance_A4F20179C01 |
| Disposable root | C:\FPFPhase2AclAcceptance-A4F20179C01 |
| Retained evidence | C:\FPFPhase2AclEvidence-A4F20179C01 |
| Ledger | root\data\synthetic.sqlite3 |
| Activation | root\data\synthetic.sqlite3.activation.json |
| Configuration | root\code\local_config.py |
| Runtime | root\runtime\Scripts\python.exe |
| Destination | https://127.0.0.1:18879 |
| Devices / employees | SCM_SYNTH_01..12 / SCM_EMP_01..12 |
| Both API credential literals | SYNTHETIC_SCM_ONLY |

There are no service/database/endpoint/credential overrides. Device fetching is a
synthetic adapter; sockets accept only the literal IPv4 loopback destination,
no DNS, redirects, UDP or device sockets. Native Windows identity lookup/S4U can
contact an authorized domain controller. This is not a firewall sandbox against
a malicious administrator or a compromised approved dependency.

Existing service/root/evidence collisions are NO-GO. Never delete old evidence to
reuse this identity. Another complete acceptance run needs separately reviewed
fixed identifiers and fresh checksums. No IT-SUPPORT access is needed or allowed.

## Prerequisites: prepare separately, not as part of this repair

1. An administrator must separately approve/provision machine-wide Python
   3.11.9 x64, normally C:\Program Files\Python311\python.exe, from verified
   official installation media. Verify the installer Authenticode signature
   and organization-approved hash before installation. Do not elevate the existing
   user-owned AppData interpreter or repair its ACLs to make it pass.
   SYSTEM, Administrators or TrustedInstaller must own the base runtime, stdlib
   and ancestors; untrusted write/replacement access is rejected BEFORE execution.
   A user-owned runtime or a venv as the base is NO-GO.
2. Approved machine-wide Git/OpenSSL at C:\Program Files\Git. The harness makes
   a disposable three-day TLS certificate; it changes no machine trust store.
3. Frozen CPython 3.11 x64 binary wheels for
   docs\requirements-windows311-accepted.txt. Obtain/review as an ordinary user
   from an approved source. No dependency installation was performed during this repair.
   If separately approved, use the existing accepted development interpreter:

~~~powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
.\.acceptance\acl-compat\.venv\Scripts\python.exe -I -B -m pip --isolated download --only-binary=:all: --index-url https://pypi.org/simple --dest .acceptance\scm-wheels -r docs\requirements-windows311-accepted.txt
$Wheels = 'C:\GitHub\biometric-attendance-sync-tool\.acceptance\scm-wheels'
$Rows = Get-ChildItem -LiteralPath $Wheels -File -Filter '*.whl' | Sort-Object Name | ForEach-Object {
  (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $_.Name
}
[IO.File]::WriteAllLines((Join-Path $Wheels 'SHA256SUMS.txt'),$Rows,[Text.UTF8Encoding]::new($false))
Get-FileHash -LiteralPath (Join-Path $Wheels 'SHA256SUMS.txt') -Algorithm SHA256
~~~

Have an independent reviewer approve provenance, every wheel hash and the
manifest SHA-256; retain that approved digest separately from the writable
wheelhouse. Hashing downloads alone does not establish provenance. No missing
wheel, changed pin, source build or authenticated production package index is
allowed. Preflight requires the independently approved lowercase digest.
Prepare copies and verifies those exact wheel bytes into protected root\wheels,
then pip installs offline/binary-only, checks every accepted pin and pip check.

4. An independently approved enabled ordinary Windows USER SID, not an
   administrator/service/group SID. For MYFPF\s.taskea, its identity resolution
   and logon on ZUHAIR-PC must be separately authorized. Resolve and cross-check:

~~~powershell
$Account = 'MYFPF\s.taskea' # Only with identity-owner approval for this PC.
$Sid = ([Security.Principal.NTAccount]$Account).Translate([Security.Principal.SecurityIdentifier]).Value
([Security.Principal.SecurityIdentifier]$Sid).Translate([Security.Principal.NTAccount]).Value
Get-CimInstance Win32_UserAccount -Filter ("SID='" + $Sid + "'") | Select-Object SID,SIDType,Disabled
~~~

The actual user must independently run whoami /user and whoami /groups.
The configured account must satisfy the unchanged monitoring policy and S4U/Authz
checks under LocalSystem. Missing authorized logon/domain access is a blocker;
do not create accounts, grant rights, save passwords or substitute an admin token.
SID authorization goes into administrator-controlled receipt/config. SID values
are identifiers, not credentials; keep identity inventory/evidence private.
The monitor cannot write this configuration or authorize recovery operations.

5. A local elevated administrator for the later authorized SCM/ACL stages.
   Only disposable ACLs change then. No machine-wide security weakening is required
   or permitted. No existing worker may use the application's global mutex.

For IT's separately authorized machine-wide Python preparation only (DO NOT run
as part of this repair), use independently verified official 3.11.9 x64 media:

~~~powershell
$Installer = 'C:\ApprovedMedia\python-3.11.9-amd64.exe'
$ApprovedInstallerSha256 = '<independently approved lowercase SHA256>'
if ((Get-AuthenticodeSignature -LiteralPath $Installer).Status -ne 'Valid' -or (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ApprovedInstallerSha256) { throw 'installer trust not approved' }
$Install = Start-Process -FilePath $Installer -WindowStyle Hidden -Wait -PassThru -ArgumentList @('/quiet','/norestart','InstallAllUsers=1','TargetDir="C:\Program Files\Python311"','Include_pip=1','Include_launcher=0','AssociateFiles=0','PrependPath=0','AppendPath=0','Shortcuts=0','Include_test=0')
if ($Install.ExitCode -ne 0) { throw 'installation incomplete; review exit code; no automatic reboot permitted' }
~~~

The independent media approval must include the expected Python Software Foundation
signer. Preflight then validates runtime ownership, rights, ancestors and actual
3.11.9 x64 version before any elevated Python execution. Do not use ACL repairs,
execution-policy bypasses or a user-profile installation as a substitute.

## Stage 1: read-only preflight

First verify the entire checked-out package against SHA256SUMS.txt independently.
Compare the manifest digest with the separately reviewed commit/review record.
This inline check does not execute package scripts:

~~~powershell
$Pack = 'C:\GitHub\biometric-attendance-sync-tool\acceptance\windows_scm'
$Seen = @{}
Get-Content -LiteralPath (Join-Path $Pack 'SHA256SUMS.txt') | ForEach-Object {
  if ($_ -cnotmatch '^([0-9a-f]{64})  ([A-Za-z0-9_.-]+)$') { throw 'invalid manifest row' }
  $ExpectedHash=$Matches[1]; $Name=$Matches[2]
  if ($Name -eq 'SHA256SUMS.txt' -or $Seen.ContainsKey($Name)) { throw 'invalid manifest entry' }
  $Seen[$Name]=$true
  if ((Get-FileHash -LiteralPath (Join-Path $Pack $Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ExpectedHash) { throw 'package hash mismatch' }
}
if ((Get-ChildItem -LiteralPath $Pack -File).Count -ne ($Seen.Count+1)) { throw 'unexpected package files' }
Get-FileHash -LiteralPath (Join-Path $Pack 'SHA256SUMS.txt') -Algorithm SHA256
~~~

Do not execute an unverified driver elevated. In a no-profile PowerShell:

~~~powershell
$SourceRunner = 'C:\GitHub\biometric-attendance-sync-tool\acceptance\windows_scm\Invoke-Acceptance.ps1'
$Common = @{
  PythonExe = 'C:\Program Files\Python311\python.exe'
  Wheelhouse = 'C:\GitHub\biometric-attendance-sync-tool\.acceptance\scm-wheels'
  WheelhouseManifestSha256 = '<independently approved lowercase SHA256>'
  MonitorAccount = $Account
  MonitorSid = $Sid
}
& $SourceRunner -Stage Preflight @Common
~~~

PASS requires the pinned application, harness-only Git delta, fresh fixed paths,
NTFS, exact account/SID, trusted Python/Git/OpenSSL, approved frozen wheel bytes,
unused port/service and absent global mutex. It enumerates executable AND command
line for all processes. Missing/inaccessible metadata, ambiguous aliases or an
acceptance reference blocks the stage. Only the current controller and the known
System/Idle kernel pseudo-processes are exempt. It creates no mutex, files, service,
ACL or SQLite state. Non-admin preflight may legitimately fail on unreadable metadata.
Do not add exclusions to make it pass; obtain a reviewed readable inventory in the
separately authorized administrator session, or record NO-GO.

## Stage 2: privileged preparation and registration (separate authorization)

In an elevated no-profile PowerShell, repeat approved variables above:

~~~powershell
$Approval = 'ISOLATED SCM A4F20179C01'
& $SourceRunner -Stage Prepare @Common -Approval $Approval
$Runner = 'C:\FPFPhase2AclAcceptance-A4F20179C01\tools\Invoke-Acceptance.ps1'
& $Runner -Stage Register -Approval $Approval
~~~

Prepare exports the pinned application; copies/hash-pins reviewed harness files;
creates a protected venv; installs approved wheels offline; provisions only the
fresh synthetic ledger and activation. The directory is protected/Admin-owned
with SYSTEM/Admin FullControl and monitor RX without propagation. The ledger has
inherited SYSTEM/Admin FullControl plus explicit monitor R. Configuration,
activation, TLS key and other data children have no monitor exception. Public
code/runtime/tools allow Users RX, never write. Registration is one Manual,
LocalSystem service with the exact fixed executable/command/display name.

Use only the protected installed Runner for subsequent stages. Failed preparation
before a working runtime/receipt exists requires a separately reviewed recovery;
do not manually delete unidentified leftovers or rerun Prepare over existing paths.

## Stage 3: controlled execution

~~~powershell
& $Runner -Stage Run -Approval $Approval
~~~

PASS requires actual SCM Running/PID, actual S-1-5-18, exact executable/config,
12 synthetic fetches and 12 local TLS POSTs, 13 total records/12 synced/one cutover
hold, DELETE journaling, no recovery tables and both recovery flags false.
It observes the exclusive expected mutex owner; stops the service during a bounded
real RESERVED SQLite writer transaction; restarts and proves zero replay and
preserved counts/checkpoints/holds/activation. It leaves the service running for
the monitor probe. Finish promptly; the synthetic tick lease expires at 15 minutes.

In a separate authorized NONADMIN session as the actual approved monitoring user:

~~~powershell
whoami /user
whoami /groups
& 'C:\FPFPhase2AclAcceptance-A4F20179C01\runtime\Scripts\python.exe' -I -B 'C:\FPFPhase2AclAcceptance-A4F20179C01\tools\scm_host.py' monitor
~~~

Expect PASS JSON after 45 seconds: actual SID/group/privilege/Authz maximum access,
directory RX/ledger R, individual mutation-right opens denied, config/activation
read denied, concurrent SQLite reads and SQLITE_READONLY on UPDATE. OPEN_EXISTING
probes request rights without changing ACLs/files. Copy the complete JSON to the
admin session; the operator must attest to its observed origin. Clipboard JSON
alone is not cryptographic proof of a logon. Unavailable actual token is NOT TESTED,
never replaced by mocked tests, S4U alone or an administrator.

## Stage 4: evidence, interruption and recovery

~~~powershell
$MonitorResult = Get-Clipboard -Raw
& $Runner -Stage Evidence -Approval $Approval -MonitorResultJson $MonitorResult
Get-Content 'C:\FPFPhase2AclEvidence-A4F20179C01\summary.json'
~~~

Omit MonitorResultJson when no actual report is available; this produces NO-GO
evidence suitable for safe cleanup. Missing/failed run evidence never becomes PASS.
Collection stops only the recognized service, claims quiet worker/mutex leases,
and writes to root\evidence-staging\<new UUID>\payload. A read-only SQLite source
is backed up through sqlite3.backup and integrity-checked; its activation is copied.
Required files, receipt identity, exact file set and SHA-256 hashes are validated
before a same-volume atomic rename publishes the fixed Evidence directory.
No existing final evidence is overwritten.

If collection fails BEFORE publication: preserve everything, inspect the error,
resolve its prerequisite without weakening checks, then rerun Stage Evidence
with the same approved report (or omit it for NO-GO evidence). A new UUID is used;
all recognized prior attempts, including partial manifests and files, are archived
byte-for-byte under final evidence\partials. Empty UUID directories or an exact
prefix of the initial identity marker are recognized only under the protected
receipt-owned staging parent; unexpected identities/payloads/owners/reparse points
are refused. No failed attempt is deleted or silently resumed in place.

If interrupted AFTER atomic publication: DO NOT retry Evidence or remove the
final directory. Stage Cleanup first validates the published manifest and proves
all partial attempts are archived. It can proceed even if Evidence's last command
did not return. Partial files in a pre-existing legacy final directory, unexpected
objects, tampering, unreadable processes or invalid ownership require review;
no bypass/automatic adoption is provided.

## Stage 5: cleanup and independent verification

~~~powershell
& $Runner -Stage Cleanup -Approval $Approval
Get-Service -Name 'FPFPhase2AclAcceptance_A4F20179C01' -ErrorAction SilentlyContinue
Test-Path 'C:\FPFPhase2AclAcceptance-A4F20179C01'
Test-Path 'C:\FPFPhase2AclEvidence-A4F20179C01\sha256.json'
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -eq 18879
~~~

Expect no service, False test-root, True retained manifest and no listener.
Cleanup checks receipt/source/config hashes, exact service identity, ownership,
reparse points, preparation inventory/known outputs, evidence hashes and partial
preservation, then stops/deletes only this service. Immediately before root deletion,
it repeats process and evidence checks while retaining create-only quiet leases.
No unrelated process is killed. The only forced process kill in the package is
Native's timeout of its own child held by its original Process handle.
If service deletion is pending, close management handles and retry.
Never use taskkill, wildcard removal, takeown or ACL changes to bypass refusal.
Evidence, base Python, wheelhouse, repository and Windows event logs are retained.

## Mutex protection and remaining concurrency limits

Every privileged stage has a separate create-only Operator mutex. Provisioning
and the SCM service atomically create a separate Worker lease and the unchanged
global production mutex with initial ownership. Any existing object/access denial
is a hard failure; the harness never waits for or adopts another owner. The service
holds both across ticks, receiver startup/shutdown and the original wrapper's
recursive same-thread application locks. Ownership is checked before/after ticks
and throughout the synthetic writer window. Controller checks combine SCM/process
identity with read-only NtQueryMutant owner PID/TID queries. Owner loss/query failure
aborts; the contender never creates/acquires the global mutex.

Evidence/Cleanup hold quiet leases after shutdown so another harness worker cannot
start during collection/deletion. Registration/restarts repeat process/mutex checks.
The Native API owner-query behavior was tested with a unique local unit-test mutex;
actual Global object permissions under LocalSystem remain an acceptance prerequisite.
See the primary [Native API declarations](https://github.com/winsiderss/systeminformer/blob/master/phnt/include/ntexapi.h).
Unsupported owner queries fail closed.

The harness cannot prevent an unrelated administrator, scheduler or an unmodified
external application from starting a production worker between stages or after a
crash releases kernel handles. While held, the global mutex makes that worker fail;
after release, an external worker may acquire it. No observer can revoke a third
party's scheduling authority. Therefore require an exclusive authorized host-use
window covering Prepare through Cleanup, with no other worker start/recovery tasks.
Do not stop/alter existing services to establish that window within this procedure.
If it cannot be assured, NO-GO on this PC: use a separately approved isolated Windows
host with a newly reviewed host restriction. Path aliases, inaccessible protected
processes and adversarial privileged changes cannot be resolved by guessing.

## Optional reboot: separate approval, never automatic

Initial acceptance does not require reboot. Before Evidence, only after explicit
approval that other work may be interrupted:

~~~powershell
& $Runner -Stage EnableReboot -Approval $Approval -RebootApproval 'APPROVED OPTIONAL REBOOT A4F20179C01'
# A human performs only the separately approved Windows reboot. No reboot command in this pack.
& $Runner -Stage VerifyReboot -Approval $Approval
~~~

EnableReboot records boot/PID/state and verifies Automatic; on failure it restores
Manual. VerifyReboot requires a new boot and fresh process/cycle, zero replay and
unchanged durable state. Its finally block restores AND verifies Manual even if
receipt, ownership or verification fails. Repeat the actual monitor probe afterward.
Reboot evidence needs separate reviewer signoff; regular summary remains NOT TESTED.

If the operator closes the shell, power fails, or restoration itself fails:

~~~powershell
& $Runner -Stage RestoreManual -Approval $Approval
~~~

If hashes are unavailable, a separately authorized administrator can use this
independent recovery after checking the reviewed fixed values:

~~~powershell
$Expected = '"C:\FPFPhase2AclAcceptance-A4F20179C01\runtime\Scripts\python.exe" -I -B "C:\FPFPhase2AclAcceptance-A4F20179C01\tools\scm_host.py" service'
$Name = 'FPFPhase2AclAcceptance_A4F20179C01'
$Owned = Get-CimInstance Win32_Service -Filter ("Name='" + $Name + "'")
if ($null -eq $Owned -or $Owned.PathName -cne $Expected -or $Owned.StartName -ne 'LocalSystem' -or $Owned.DisplayName -cne 'Disposable Phase 2 ACL acceptance A4F20179C01') { throw 'unrecognized service; no change permitted' }
Set-Service -Name $Name -StartupType Manual
if ((Get-CimInstance Win32_Service -Filter ("Name='" + $Name + "'")).StartMode -ne 'Manual') { throw 'MANUAL RESTORATION FAILED' }
~~~

Independently compare the service's full ImagePath, LocalSystem account and unique display name
against this document before any Set-Service. Then set ONLY this recognized service
to Manual and verify Win32_Service.StartMode equals Manual; if it differs, stop and
escalate for review. Never alter an unrecognized service. An Automatic service blocks
ordinary stages until restored. No reboot or production operation is authorized here.

## Final GO / NO-GO

GO for separately authorized elevated ISOLATED execution only when:
- Package checksums reviewed; protected Python and approved frozen wheels available.
- Actual approved user SID/logon available; runtime/S4U/ACL policy prerequisites met.
- Exclusive host-use window assured; readable process inventory and preflight PASS.
- Administrator explicitly authorizes the fixed-target stages.

Any missing condition is NO-GO. Actual SCM/LocalSystem, monitoring token, global
permissions, shutdown/restart and reboot are NOT certified by preparer unit tests.
Acceptance success additionally requires all Stage 3/monitor/evidence/cleanup checks.
It does not authorize production deployment, IT-SUPPORT modifications, ERPNext writes,
historical investigation or historical recovery. No code push/merge is required.
