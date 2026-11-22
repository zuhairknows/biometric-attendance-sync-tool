# Approved two-record confirmation

Operator-confirmed completed operation. Retained for audit and idempotent confirmation only; do not rerun without separate authorization. See [current Phase 1 status](phase1-status.md).
This tool is fixed to ledger 270480 / EMP-CKIN-10-2026-052260 and
270334 / EMP-CKIN-10-2026-052130, the existing activation cookie, destination
fingerprint and 2026-10-17 08:58:22 boundary. It cannot accept other record IDs.

Copy only these reviewed files into a protected administrator-controlled directory
`C:\ProgramData\ERPNextBiometricConfirmation`, without changing the deployed checkout:

- `scripts/confirm_two_uncertain.py`
- `durable_confirmation.py`
- Existing unchanged `scripts/verify_integration_get_only.py`

Keep all three files adjacent. Verify their reviewed SHA-256 hashes after transfer.

| File | Reviewed SHA-256 |
| --- | --- |
| confirm_two_uncertain.py | 8305E0F723D775D54306D7A45AF293D102EFA99359CA009B3AFD9131FE16F8E2 |
| durable_confirmation.py | 873E4E91AB07EA8F8570FEFC941595788194ACA65B112F99E83342177FC5727A |
| verify_integration_get_only.py | F5FBA92A29C7D21DB0CACBD2FBDE306E6A1C264DEFFE3295A2A1DAB07DABEC4B |

The helper additionally verifies the pinned 4256cd6 ledger and startup module hashes
(normalizing Windows CRLF) before importing those two modules by explicit path.
It does not import local_config, the service wrapper, delivery loop or device code.
Credentials are read through the existing AST-only parser; executable, ambiguous or
unsupported configuration is refused. Credentials and response details are never printed.
The source must still have literal ERPNEXT_VERSION=15 and
DURABLE_CONFIRM_READS_VERIFIED=False. Configuration is not changed.

## Proposed production command (not executed)

Run in an administrator PowerShell session only after approval:

```powershell
& 'C:\GitHub\biometric-attendance-sync-tool.venv\Scripts\python.exe' -I -S -B 'C:\ProgramData\ERPNextBiometricConfirmation\confirm_two_uncertain.py' --runtime-root 'C:\GitHub\biometric-attendance-sync-tool' --config 'C:\GitHub\biometric-attendance-sync-tool\local_config.py' --db 'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3' --apply-approved-two
if ($LASTEXITCODE -ne 0) { throw 'Confirmation refused; inspect both ledger statuses. Do not resend.' }
```

Omit `--apply-approved-two` for a preview: the ledger opens read-only.
Do not run against a copied backup expecting production reconciliation.

## Safety and acceptance

- The existing bounded HTTPS transport connects only to erp.myfpf.com, sends GET,
  verifies TLS, ignores proxy settings, refuses redirects, and checks authenticated
  identity attendance.sync@myfpf.com. No insertion endpoint or POST transport is used.
- Require one exact active Employee mapping and one exact blank-string Checkin
  identity, across all devices, with the approved document name and source device.
  NULL, duplicate results, missing mappings or mismatches refuse confirmation.
- Reuse prior administrator-approved integration-account read-completeness evidence;
  a filtered API response alone is not independent completeness proof.
- A short engine-owned transaction verifies the unchanged identity, state, attempt,
  lease and activation binding. Only retry/confirmation_pending with no lease can
  transition to duplicate/destination_confirmed, with a delivery_attempts audit entry.
  No submission attempt counter is incremented and no evidence is deleted.
- This is a confirmation operator, not a second delivery worker: it does not acquire
  a submission lease or invoke the globally locked worker. Concurrent worker changes
  cause refusal; transactions are short and retain SQLite's existing busy timeout.
- PASS: exit 0 and both IDs report CONFIRMED or ALREADY_CONFIRMED. Repeated execution
  performs fresh reads but creates no extra audit entry for completed records.
- Each record commits independently. A later failure can leave the first confirmed;
  preserve that result and inspect both statuses before repeating the same command.
- This does not verify effective server hooks, alter global confirmation policy,
  change watermarks or release historical holds. Service restart is unnecessary.

The existing verified online backup remains the rollback evidence. Do not restore an
older ledger over the running service to undo confirmation. Stop and investigate any
refusal; do not requeue or resubmit either punch.
