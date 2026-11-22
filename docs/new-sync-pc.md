# Moving the sync service to a new PC

How to replace the current sync PC (manual Phase 2 install in
`C:\Program Files\FPFBiometricSyncPhase2`) with a new PC installed by the portable
installer. The new PC starts with an empty ledger and a cutover time: punches before
the cutover are held, never re-sent, because the old PC already delivered them. The
old PC keeps running past the cutover, so there is an overlap and no gap; punches
both PCs send are recognized by ERPNext as duplicates and recorded as delivered.

Read `docs/portable-installation.md` for what each installer stage checks. The
installer stages have not yet been run on a real PC (`docs/portable-acceptance.md`),
so do Preflight and Prepare on the new PC first as a trial, before choosing a cutover.

## 1. Prepare the new PC

* Windows 11 x64 workstation edition (Preflight refuses Windows 10 and Server),
  Windows PowerShell 5.1, local NTFS disk, at least 5 GB free.
* Official CPython 3.11.9 x64 installed for all users under `C:\Program Files`
  (only Administrators can write there). Nothing else: the package carries its wheels.
* Network: TCP to every device (FP1 10.0.0.x ports 4371-4377, FP2 10.0.1.x ports
  4378-4382) and HTTPS to the ERPNext site. Test before going further:

~~~powershell
Test-NetConnection 10.0.0.20 -Port 4371
Test-NetConnection erp.myfpf.com -Port 443
~~~

* Keep it powered and awake: power plan set to never sleep.

## 2. Build and copy the package (on the development PC)

Commit everything first; a dirty tree cannot be installed. Then build (see
`docs/portable-installation.md`, *Reproducible local build*) and note the SHA256:

~~~powershell
Get-FileHash .\dist\fpf-biometric-sync-2.0.0-rc3.zip -Algorithm SHA256
~~~

Copy to the new PC, into an Administrators-only folder such as `C:\ApprovedMedia`:
the ZIP, its SHA256, and `deployment\Install-FPFBiometricSync.ps1` taken out of
the ZIP after checking the ZIP's hash.

## 3. Write the private config

Start from `deployment/local_config.py.template` and save it as
`C:\ApprovedPrivateConfig\local_config.py` (folder readable by SYSTEM and
Administrators only). Copy from the old PC's `local_config.py`:

* `ERPNEXT_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` (the sync API user);
* `IMPORT_START_DATE`, `devices` and `shift_type_device_mapping` exactly as they are;
* `SHIFT_SYNC_IGNORED_DEVICES` for any reader that is knowingly offline.

Set `DURABLE_DB_PATH` and `LOGS_DIRECTORY` under the new data root
(`C:\ProgramData\FPFBiometricSync\data\sync.sqlite3` and `...\logs`). Keep the
resend and shift-sync settings from the template. Values must be plain literals;
Prepare rejects anything else, a mistyped setting or a device in
`SHIFT_SYNC_IGNORED_DEVICES` that is not in `devices` (Preflight checks only the
config file's permissions).

## 4. Trial: Preflight and Prepare (no service, no network)

Elevated Windows PowerShell 5.1 without profile, as in
`docs/portable-installation.md`: run `-Stage Preflight`, then `-Stage Prepare
-Approve`. Store the printed receipt path. Neither stage registers a service or
contacts devices or ERPNext.

## 5. Cutover

Pick a time on a working day when people are punching (Install requires a real
delivery within 10 minutes) and when every device answers.

1. On the old PC, open the dashboard: *Pending* close to 0 and *Retry* only the
   non-employee cards. The old service keeps running.
2. On the new PC: `-Stage Activate -Receipt <receipt> -Approve -ExclusiveHostWindow
   -Cutover '<about 10 minutes ahead, YYYY-MM-DD HH:MM:SS>' -ReviewReference '<your reference>'`.
3. Wait until the cutover time has passed, plus one old-PC cycle (5 minutes), so the
   old PC has delivered everything up to the cutover.
4. On the old PC, elevated:

~~~powershell
Stop-Service ERPNextBiometricPushService
Set-Service ERPNextBiometricPushService -StartupType Manual
~~~

   Only one PC may read the devices at a time; ZKTeco readers accept one connection.
5. On the new PC: `-Stage Install -Receipt <receipt> -Approve -ExclusiveHostWindow
   -CheckNetwork`. It registers and starts the service and waits for fresh captures
   from every device and an ordinary delivery. On failure it stops the new service
   and keeps all data; restart the old one with `Start-Service` and investigate.

Punches made between the cutover and the old service stopping are sent by both PCs;
the new PC records them as duplicates.

## 6. After Install

* Dashboard on the new PC (dashboard bundle, elevated then normal PowerShell):
  `Install-MonitorSnapshotTask.ps1`, `Install-MonitorSnapshotTask.ps1 -Apply`, then
  `Install-MonitorDashboard.ps1` with no arguments.
* Within an hour every Shift Type's *Last Sync of Checkin* should move to the
  present (`logs.log` event `shift_watermark`).
* Keep the old PC as it is, service on Manual, for two weeks. Do not delete its
  ledger or config; it is the record of everything sent before the cutover.
