# Release acceptance checklist

Automated regressions do not certify actual Windows SCM, LocalSystem, S4U or a clean
PC. Record exact package, receipt, environment, date and evidence per executed case.

| Scenario | Required actual evidence | New installer execution |
|---|---|---|
| Clean supported PC | Prepare/Activate/Install with synthetic devices/local TLS and protected runtime | NOT RUN |
| Missing prerequisites | Wrong OS/Python/architecture, runtime/config trust and missing/changed wheels fail before service mutation | NOT RUN |
| Repeated install | Existing service/data collisions refused without changing existing resources | NOT RUN |
| Managed upgrade | Reviewed same-core/config pair, current records and new deliveries retained | NOT RUN |
| Failed upgrade | Inject startup/durable failures on disposable host; compatible sender resumes CURRENT ledger | NOT RUN |
| Later rollback | Post-upgrade records/activation preserved, no backup restored | NOT RUN |
| Restart | Bounded stop, mutex handover, uncertainty/holds/checkpoints preserved, no replay | NOT RUN |
| Reboot | Separate approval; delayed-auto, actual token, state retention and delivery | NOT RUN |
| Manager | Actual ordinary token and read-only/denied mutations, fresh job, recovery routes denied | NOT RUN |
| Recovery disabled | Both flags false; no sr_* created by ordinary startup | NOT RUN |
| Authorized recovery | Synthetic/local TLS, selected dates, budgets, duplicate uncertainty, cancellation and audit | NOT RUN |
| Uninstall | Only recognized service removed; ledger/config/backups/activation/other services retained | NOT RUN |
| Legacy migration | Separate exact source/config/ACL/state/current-ledger rollback review | NOT RUN |

GO requires independent review of the clean commit/ZIP digest, wheel provenance,
protected runtime/ancestors, config and approved identities; readable process
inventory/exclusive host window; the actual disposable Windows matrix above;
and separate production ERPNext/device/identity checks. Recovery stays disabled.

October 24 IT-SUPPORT success is operator-supplied evidence for the manually deployed
release, not execution of this installer. No new installer Windows scenario is
marked passed. Missing pyzk build log remains an explicit provenance limitation.
