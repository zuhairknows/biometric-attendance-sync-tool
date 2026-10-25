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
