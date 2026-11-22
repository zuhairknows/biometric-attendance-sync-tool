# Historical recovery operating boundaries

Installer defaults and validators require HISTORICAL_RECOVERY_ENABLED=False and
HISTORICAL_RECOVERY_COORDINATION=False. Ordinary installation acceptance is not
historical recovery authorization.

Reuse the existing guides:
- phase2a-historical-investigation.md and phase2a-production-read-only-verification.md:
  selected dates, bounded retrieval, verified contracts and private source/results.
- phase2b-controlled-recovery.md and historical-recovery-deployment.md:
  independent release/author/contract authorization, duplicate/uncertainty checks,
  finite budgets, explicit hold approval, cancellation and private audit.
- phase2c-manager.md: distinct monitoring, investigation and service-owned recovery.

Only the existing service writer may run an authorized recovery lane. Never run
another synchronization/recovery worker. Cancellation does not erase uncertainty.
Preserve intents, audit, holds, checkpoints and private exports; an older sender
without intent fencing is not a safe fallback merely because its schema is 1.

Recovery acceptance uses synthetic devices, local TLS destination and disposable
storage/approved identities. Existing historical/release/service-recovery/preview/
deadline/monitor-only regressions remain authoritative automated safeguards.
Actual Windows tokens/SCM acceptance is separate.

IT-SUPPORT historical recovery remains disabled and not production-accepted. This
installer exposes no enable-recovery switch.
