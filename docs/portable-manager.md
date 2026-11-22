# Manager operation

The release includes the independent loopback Flask/Waitress manager with accepted
dependencies in the protected runtime. Its launcher starts no synchronization
worker. Run under the approved normal user, not elevated to bypass an error.

For a NEW profile only:
~~~powershell
$Code='<installed version code directory>'
& "$Code\deployment\New-MonitorProfile.ps1" -Ledger '<existing absolute ledger>'
& "$Code\deployment\Start-Monitor.ps1" -ProfilePath '<printed private profile path>'
~~~
The helper creates one fresh user-private directory, with user/SYSTEM/Administrators
access. It never edits ledger ACLs or existing profiles. Use an existing approved
profile directly to preserve the working manager setup.

Ordinary users need separately approved actual directory traverse/read and ledger
read rights. SYSTEM/Administrators-only storage does not provide that access.
Do not configure an administrative monitor SID. The optional reader policy requires
ordinary identity approval, minimal directory RX/ledger R without propagation,
S4U/Authz and actual-token acceptance. It grants no activation/config/journal/WAL/SHM
exception. Do not change journal mode to work around monitoring.

Click Refresh diagnostics, wait for completion, Continue to results. Browser reload
does not start a new job. Require a new reference and Dashboard UTC timestamp, then
read the matching private monitor-<reference>.jsonl. Its field is source_binding,
not monitor_source. Detailed operation/extended-code flags distinguish source open,
reference schema and later checks. A fresh standalone process cannot certify the
running manager's context. Preserve a fresh failure then relaunch ONLY the manager
with the same normal token/profile if an exact-environment standalone probe passes.

Keep --monitor-only: investigation/recovery routes remain denied. Optional user-logon
shortcuts need separate approval; no elevated task/service or startup entry is added.

## Monitoring dashboard bundle

For the manually installed Phase 2 folder (`C:\Program Files\FPFBiometricSyncPhase2`)
and for portable installations alike, `deployment\Install-MonitorDashboard.ps1`
installs the monitoring-only manager beside it, from a bundle with `code\` and
offline `wheels\` (Flask, Waitress, Requests and their dependencies), into
`%LOCALAPPDATA%\FPFBiometricManager` (`code` + `runtime`, the layout
`Start-Monitor.ps1` expects). It creates a private profile with
`New-MonitorProfile.ps1 -DeviceConfig` and a `Start-Dashboard.cmd`. It never touches
the service, its folder, the ledger or ACLs. Run it as the normal dashboard user.

The dashboard adds, from stored data only:

* **Shift sync**: next `last_sync_of_checkin` estimate per mapping, each device's
  status (`ok`, `scan_failed`, `stale`, `ignored`, `never_scanned`), punches waited
  for and not waited for, and the last date confirmed by ERPNext per shift.
* **Device health**: time since the last successful pull, punches captured in the
  last 24 hours, punches dated more than 10 minutes in the future (device clock).
* **Unknown cards**: the 25 device user IDs with most `unmatched_employee` punches.
* **Not delivered, by reason**: counts per state and error category.

On the sync PC itself the live ledger folder stays private (the service refuses to
start if another account can read it). `Install-MonitorSnapshotTask.ps1` (elevated,
dry run unless `-Apply`) registers the scheduled task *FPF Biometric Monitor
Snapshot*, which runs `monitor_snapshot_refresh.py` as SYSTEM with the service's
Python every 10 minutes. It copies the ledger with the online backup API from a
read-only connection into a staging file, checks it, and swaps it into
`%ProgramData%\FPFBiometricMonitor\snapshot\sync.sqlite3` in one backup step, with
a secret-free `local_config.monitor.py` and `refresh-status.json`. That folder is
protected: SYSTEM and Administrators full control, the dashboard user read only; the
helper sits in `bin\`, writable by administrators only. The service's Python and
`local_config.py` are found from the registered service command, which covers both
layouts (`-AppRoot` forces the Phase 2 folder). `Install-MonitorDashboard.ps1` then
defaults to that snapshot, so on the sync PC it needs no arguments. `-Remove -Apply`
deletes the task and the folder. The service, its folder and the live ledger ACLs are unchanged.

To review a production ledger on another PC, run `Export-MonitorSnapshot.ps1`
(elevated) on the sync PC. It copies the ledger with SQLite's online backup API from
a read-only connection and writes `local_config.monitor.py` without the API key,
secret or device passwords. Install the dashboard on the review PC with `-Ledger`,
`-DeviceConfig` and a separate `-ProfileDirectory` pointing at that snapshot.
Never point the manager at a ledger over a network share.
