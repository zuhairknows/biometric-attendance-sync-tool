# Monitoring ACL compatibility: development candidate

This change extends baseline `69ba77f87e73b13ba89ac44bbbfa9c57332a3e84` on
`recovery`. It is not production deployment or recovery authorization.
The baseline's 755-test/fresh-clone evidence remains historical evidence for
that baseline, not certification of this new commit.

## Setting and scope

An administrator may add **one independently verified ordinary user SID** to the
service's private `local_config.py`:

```python
# Replace the placeholder with the independently verified SID for MYFPF\s.taskea.
DURABLE_MONITOR_READONLY_SID = '<VERIFIED_CANONICAL_USER_SID>'
HISTORICAL_RECOVERY_ENABLED = False
HISTORICAL_RECOVERY_COORDINATION = False
```

The placeholder deliberately fails validation. Do not copy an invented SID or
resolve a supplied display name automatically into an authorization. With the
setting absent/None, the old strict private-storage policy is unchanged. The
setting grants no Windows permission. It cannot come from a manager profile,
browser, request file or API. It is never added to the unrestricted service SID.

Only the configured canonical ledger and its containing directory receive the
exception: directory maximum RX/Synchronize (`0x1200A9`), ledger maximum
R/Synchronize (`0x120089`). Explicit or inherited read-only ledger ACEs are
supported; monitor propagation/inherit-only flags are refused. Generic masks are
mapped before comparison. A Deny cannot excuse an excessive Allow. SYSTEM and
Administrators remain the only writers/owners of the directory, ledger and its
immediate children. Unknown Allow principals, unsupported/object/callback ACEs,
unsafe owners, unprotected parent, nonlocal/non-NTFS volumes, cloud ledger paths
and reparse points fail closed. Every immediate child is inspected; activation
files, journals, WAL/SHM and other children gain **no** monitor read exception.

The supplied production directory/ledger ACL shapes pass the synthetic policy
tests. Compatibility still depends on the actual SID, group membership,
privileges, inheritance flags, sibling files and installation ancestors.
In particular, a monitor read grant on the activation file is not accepted.

## Identity, effective access and configuration trust

The opt-in implementation requires the actual process token to be LocalSystem.
It resolves the SID as SidTypeUser, rejects service/machine and privileged
identities, obtains mandatory S4U groups/privileges without a SID-only fallback,
and checks local service-logon assignments for the user and its groups.
S4U/LSA resolution failure or the 15-second inspection deadline blocks startup;
no permissions are changed to make it pass. Windows may consult a domain
controller for identity/group resolution; this adds an availability dependency.

Windows Authz maximum-access evaluation checks monitor group access and the
actual writer token against each descriptor. The service configuration must be
an absolute Python module source named local_config, separate from ledger
storage, with trusted owner and strict file Allow principals. Configuration
directory writes and ancestor replacement rights are refused, including raw
grants to unknown/logon groups even if absent from the S4U context. Ancestor
owners must be SYSTEM, Administrators or Windows TrustedInstaller.

S4U is not proof of an interactive monitoring token. An actual monitor token,
including restricted/deny-only/logon groups, must still pass isolated acceptance.
ACLs and group memberships must remain administrator-controlled: this is a
point-in-time check, not protection against an administrator changing policy
after validation. Python configuration executes during the existing import;
the ACL inspection cannot make already untrusted executable configuration safe.

The monitoring SID cannot also be the configured recovery author, including an
S4U group/SID-history membership matching that author SID. Inbox,
evidence, exports, release authorization and operator validation keep their
existing separate strict policies. The monitor setting never authorizes a
request or a historical POST. Recovery remains disabled by default.

## Safe provisioning and production gate

Do not execute production changes as part of development acceptance.

1. During separately authorized read-only inventory, IT-SUPPORT must resolve
   the exact account SID and confirm it is an ordinary user, not a group,
   administrator, service account or recovery author. A read-only example is:
   ```powershell
([System.Security.Principal.NTAccount]'MYFPF\s.taskea').Translate(
  [System.Security.Principal.SecurityIdentifier]).Value
```
2. Privately capture owner, protection, complete numeric DACL/ACE flags for the
   ledger directory, ledger, every immediate child, private configuration,
   configuration directory and all ancestors. Verify group membership, assigned
   privileges/logon rights and effective access under actual tokens. Do not
   print private configuration or attendance rows.
3. Keep both existing application folders, private configuration and production
   ledger unchanged. Build a separate release directory and isolated accepted
   Python 3.11 x64 environment. An administrator alone can provision the approved
   SID later, during separately authorized deployment.
4. On a staging machine, use a uniquely named real LocalSystem SCM service,
   synthetic devices, loopback ERPNext and a disposable ledger. Preserve the
   production ACL shapes without granting new rights to make a failure pass.
   Never run beside another instance using the global durable-writer mutex.
5. Prove startup/config loading, normal capture/delivery, real monitor read-only
   SQLite/manager access, negative file-write/delete/ACL/ownership attempts,
   child isolation, mutex exclusivity, stop/interruption/restart and preserved
   holds/checkpoints/intents. Only disposable files may be write-probed.
   Reboot acceptance requires separate approval; otherwise record pending.
6. Check the ledger's existing journal mode. The release uses rollback DELETE
   journaling; do not enable WAL to work around monitoring access. Test monitor
   reads during a real isolated writer transaction and restart/hot-journal
   conditions. Do not grant WAL/SHM access automatically.
7. SCM Running alone is insufficient: verify no durable_critical startup error,
   continued synthetic polling and persisted delivery. Any unknown or excessive
   access, failed native identity lookup, configuration trust failure, or state
   loss is NO-GO. Existing production ACLs need no edit only if all checks pass.

## Validation and rollback

Focused and complete suite results and exact environment are recorded in the
companion validation report. Actual LocalSystem, approved monitoring-token,
SCM and reboot acceptance are **NOT TESTED** in development without elevation.
No production device, ERPNext endpoint, credentials or ledger was used.

For rollout/rollback use the separately approved maintenance procedure in
[historical recovery deployment](historical-recovery-deployment.md). Preserve
the current ledger and activation, holds, checkpoints and durable recovery
intents; never initialize or restore an old snapshot merely to bypass a failure.
Removing this setting restores strict validation and therefore rejects the
existing extra reader ACE. Baseline Phase 2 likewise rejects it. Rollback must
return to a separately accepted compatible service, not assume baseline Phase 2
will run with the monitoring grant. Preserve the currently working installation
until replacement and rollback have been accepted.

**Development GO** requires automated regressions to pass.
**Production replacement and live recovery: NO-GO** until separately approved
actual token/SCM/state/rollback acceptance and the existing ERPNext recovery
authorization gates are complete.
