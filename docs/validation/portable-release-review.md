# Portable release-hardening verification

Baseline: 3fbf5e967240ac9b164f64567c8a34ff81f959cc.
Local branch: codex/release-hardening.
Candidate version: 2.0.0-rc2. Not production-installation certification.

## Executed automated checks

- Complete suite: 809 tests passed; see portable-release-application-tests.txt.
  Includes 780 existing tests and 29 new hardening regressions.
- Existing SCM acceptance-harness regressions: 28 passed; see portable-release-harness-tests.txt.
- Windows PowerShell 5.1 parser and AST-extracted helpers exercised without driver stages.
- Real disposable SQLite backup/read-only checks retained source bytes, current device/
  shift state, activation binding and intent evidence; no production source was opened.
- Synthetic package tests checked byte reproducibility, frozen/hash-required wheel lock,
  missing/extra/corrupt wheels, tampered/extra source, unsafe Windows paths and private inputs.
- Failure tests covered failed stops, unknown service command, missing Python metadata,
  core/config rollback drift, private-file protection failure, atomic receipt replacement,
  native argument quoting and default-owner rejection.
- Native helper errors expose only fixed allowlisted codes; raw private stderr is not returned.
- Final focused hardening run: 29 tests passed after the runtime-revalidation review.
- Source diff confirms core service, durable ledger/delivery/startup, monitoring policy,
  historical recovery and manager application code unchanged from the baseline.
- Development test token was not an elevated administrator.

## Privileged-action review

| Action | Boundary and fail-safe behavior |
|---|---|
| Preflight | Read-only runtime/owner/ancestor/OS/NTFS/package-digest checks; reports existing service. No extraction, mutex creation, network or ledger access |
| Prepare | Explicit administrator approval; new protected version slot only; copied package rehashed before execution; no existing folder overwritten |
| File security | Normalize only fresh package/code/runtime trees before privileged execution; private config protected while empty before credentials copied; receipts in private control directory |
| Owner check | One unique empty probe; unsupported elevated default owner refused without changing machine policy; only verified empty owned probe removed |
| Dependencies | Offline binary-only hash-required install, 19 exact installed pins, pip check and whole-file SpeedFace patch comparison |
| Activation | Explicit fresh-only provision, future cutover/reference, no existing service/data/log directory; optional ordinary SID grant requires separate approval |
| Network | Explicit CheckNetwork: configured TCP ports and one TLS GET, no redirects/device commands/experimental POST |
| Registration | Fixed existing application service name; LocalSystem and delayed-auto; command binds protected runtime and entry point |
| Stop/switch | Expected SCM command/account, bounded waits and worker exit; Python inventory/global mutex open-only absence; change ImagePath only |
| Upgrade | Revalidate protected base/Python architecture and actual pins/patch before use; managed receipts and identical private config/core source; current-state SQLite backup/integrity check with activation hash; no migration/provision/restore |
| Rollback | Stop failed candidate, recheck current state/intents, restore compatible old command and verify health; never restore pre-upgrade data |
| Health | Stable actual PID/LocalSystem token and fresh logs for every configured device plus ordinary HTTP 200; critical events abort. No repeated full DB scans while waiting for logs |
| Uninstall | Recognized stopped service registration only; retains all files, DB, config, activation and backups; pending deletion refused |
| Manager | Ordinary-user launcher, monitor-only, fresh private profile helper; existing profile/manager untouched |
| Timeout | Only owned native child may be killed. Redirector child/partial output may remain; no tree deletion/adoption or forced service-worker kill |

## Remaining gates and limitations

Actual clean-PC installation, repeated install, real SCM/LocalSystem/monitor token,
managed upgrade/failure/rollback, restart/reboot, uninstallation and authorized
recovery acceptance of THIS tooling have NOT been executed. See portable-acceptance.md.
A separately approved disposable Windows host/VM and administrator are required.

Automatic upgrade is packaging-only: no unreviewed application/config changes.
Legacy services lacking trusted receipts require separate manual migration review;
there is no automatic adoption or bypass. Native timeouts and failed preparation
retain partial resources for review. Other administrators/schedulers cannot be
excluded by an observer; exclusive host use is an operational prerequisite.

No IT-SUPPORT access, service/ACL/account changes, dependency installation, reboot,
production requests, ledger writes, recovery enablement, merge or push occurred.
The earlier operator-reported production success is not new-installer acceptance.
The pyzk wheel/source content linkage is approved separately; historical build log
remains absent. No package provenance requirement was relaxed.

## Artifact verification

The final package is built from the clean local commit and approved existing wheel
bytes. It is rebuilt independently and both ZIP digests compared; the external
checksum and verification JSON are retained beside the local dist artifact.
Clean builds use captured Git blob bytes; native wrappers retain their approved
CRLF form. No venv, private configuration/profile or ledger enters the archive.

GO for independent review and separately authorized isolated Windows acceptance.
NO-GO for claiming this candidate is certified for unattended production installation.

## Development SDK observation

The existing accepted development venv has pyzk 0.9 in its original unpatched form.
It was not modified. Artifact verification applied the published SpeedFace recipe
only to a disposable copy from the approved wheel and confirmed exact resulting
bytes. The target Prepare stage applies/verifies this recipe in its fresh venv.
This is neither a target dependency installation nor hardware/SCM acceptance.
