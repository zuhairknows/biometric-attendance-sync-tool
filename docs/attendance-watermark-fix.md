# Attendance synchronization timestamp correction

Prepared on branch `recovery`; production deployment is pending.
The requested Frappe Attendance processing start date is September 30, 2026.

## Change

The durable worker now supplies its existing, validated live delivery boundary
to the Shift Type timestamp calculation. The boundary is the later of
IMPORT_START_DATE and the activation marker's cutover time, exactly as in delivery.
Unresolved punches strictly before that boundary no longer cap the timestamp.
They remain in the ledger with their original states and evidence.

Unresolved punches at or after the boundary still cap progress. Unknown timestamps,
missing devices and failed scans still block progress. No delivery, retry,
historical replay, employee matching or API payload behavior is changed.
Callers that do not supply a boundary retain the previous conservative behavior.

## Deployment and verification

On IT-SUPPORT, pull the `recovery` branch in the Git checkout. Then run the
installer from that checkout in **PowerShell as Administrator**:

```powershell
git pull --ff-only origin recovery
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_attendance_fix.ps1 -Apply
```

The checkout must already be on `recovery`. Do not pull this branch into another
branch or overwrite uncommitted work. Without `-Apply`, the installer performs
preflight checks only. It refuses unexpected or mixed installed versions, verifies
payload hashes, backs up and installs only `sync_ledger.py` and `durable_sync.py`
in `C:\Program Files\FPFBiometricSyncPhase2`, then restarts
`ERPNextBiometricPushService`. If replacement/restart fails, it attempts to restore
the original pair. It never copies `local_config.py` or opens the production ledger.
If both corrected files are already present, it makes no changes.

Pulling alone does not change the installed service, which runs from Program Files.
The service resumes its normal device polling and Frappe processing after restart;
the installer itself does not poll devices or write Frappe settings.

1. Verify the installed source on IT-SUPPORT before replacing or patching it.
   The service runs from C:\Program Files\FPFBiometricSyncPhase2, not the Git checkout.
2. Back up the two installed source files and current shift processing settings.
3. Verify Process Attendance After is September 30, 2026 on the 21 mapped shifts
   before allowing their sync timestamps to advance. A read-only check on October 27
   confirmed these settings and that the sync timestamps were still frozen.
   This date controls Attendance;
   it does not change the worker activation boundary or replay held device records.
4. Apply only the reviewed sync_ledger.py and durable_sync.py correction and restart
   the service during the deployment. Do not delete held punches or reset statuses.
5. Confirm that Shift Type timestamps advance only through the oldest unresolved
   live punch across the mapped devices. The 202 retry records in the supplied
   diagnostic snapshot can still cap this progress; they are not ignored.
6. Verify actual Attendance after a scheduled job, including overnight shifts.
   Compare samples with their source Checkins. Existing Checkins before the worker
   activation boundary may be incomplete; the code fix does not establish completeness.

Local tests use temporary SQLite and mocked requests. They do not prove deployment,
production database state, complete device coverage or correct Attendance results.
On October 27, the complete offline application suite passed: 792 tests in 196.231
seconds, including 9 attendance regression tests and 3 installer preflight tests.
Run: `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`.
The complete run used the normal Windows environment because the sandbox blocked
Windows checks, child PowerShell execution and synthetic local TLS in an earlier run.
The installer is syntax-checked and its preflight is tested with mocked Windows
commands. Real service replacement, restart and rollback still require runtime
verification on IT-SUPPORT.
