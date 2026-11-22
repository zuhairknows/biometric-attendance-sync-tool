# Phase 1 status and repository guide

Status recorded during repository cleanup on October 17, 2026. Production outcomes
below are operator-confirmed evidence, not a fresh production inspection.

## Versions and scope

| Reference | Purpose |
| --- | --- |
| `release/phase1` | Canonical operational Phase 1 source, supporting tools, tests and documentation |
| `4256cd6201b17dcda3f9d078552acf1d3c58b3f0` | Deployed durable-engine runtime baseline; unchanged by this cleanup |
| `a23b8112fd1b87f3d2bb10f798b45e150aadcd7a` | Legacy production rollback source reference |
| `feature/phase1-service-hardening` | Preserved original release-preparation branch |
| `feature/phase1-durable-sync` | Preserved earlier Phase 1 development branch |
| `feature/phase1-auto-attendance` | Undeployed watermark correction, focused tests and risk assessment |
| `feature/historical-recovery-dry-run` | Separate historical recovery preview; recovery execution remains unauthorized |

No stable branch is merged or rewritten. A branch name is not a production release
identifier: use the exact deployed commit and reviewed file hashes for diagnostics.
Historical capture/reconciliation/export utilities already committed in Phase 1
remain available, but do not authorize historical replay or recovery.

## Accepted operational state

- Core durable synchronization is running; new Checkins were independently verified.
- The two previously uncertain submissions were reconciled, per operator confirmation.
- Historical punches remain held under the clean-cutover policy.
- Effective server hooks remain unverified, with residual risk previously accepted
  for core synchronization. This is not certification of Auto Attendance safety.
- All 21 Shift Type watermarks remain frozen. The existing historical Checkins can
  become eligible when watermarks advance; the reported exposure was 13,520 records.
- Auto Attendance is **not complete**. A future-only attendance-date safeguard is
  design-only. Do not deploy the watermark correction before that safeguard is
  separately approved, deployed and verified.

## Documentation index

| Need | Document |
| --- | --- |
| Engine and ledger design | [Durable synchronization](phase1-durable-sync.md) |
| Startup, cutover and uncertain outcomes | [Service hardening](phase1-service-hardening.md) |
| Original cutover/rollback guidance | [Cutover runbook](phase1-cutover-runbook.md) |
| Completed production deployment record | [Clean-cutover procedure](phase1-production-clean-cutover.md) |
| Windows staging history | [Staging steps](phase1-windows-staging-steps.md), [SCM validation](phase1-windows-scm-validation.md) |
| Consolidated staging coverage | [Final staging procedure](phase1-final-staging-procedure.md), [readiness gates](phase1-final-readiness.md) |
| Integration-account read verification | [GET-only controls](integration-get-only-verification.md) |
| Deployed-source review requirements | [Server-source verification plan](frappe-cloud-checkin-source-verification-plan.md) |
| Completed scoped confirmation operation | [Two-record confirmation](two-record-confirmation.md) |
| Offline historical comparison | [Reconciliation runbook](phase1-attendance-reconciliation.md) |

Earlier runbooks describe their preparation-time status and contain expired,
incident-specific commands. Their original command blocks are retained for traceability;
they are **not instructions to rerun completed deployment, staging or confirmation**.
Use this status page for current scope, and obtain a new approved plan for any future
production action. The clean-cutover timestamp is not a reusable activation boundary.
Staging runners deliberately retain their exact `4256cd6` checkout and source-hash
guards; they may refuse the later `release/phase1` HEAD. Do not weaken those guards.
Any authorized future staging run must supply the original pinned source environment.

## Cleanup verification

`py -B -m unittest discover -s tests -v` passed all **426 tests** on the development
PC during consolidation. These use synthetic fixtures, mocked service operations
and localhost TLS; they do not repeat the actual production or staging SCM session.
The separate watermark branch also passed its **10 focused tests**. Runtime source
files on `release/phase1` have no Git content difference from `4256cd6`, and the three
standalone confirmation transfer hashes remain unchanged. Windows line-ending
attributes preserve the existing reviewed byte hashes on future checkouts.

## Private evidence and repository hygiene

SCM logs, ACL backups, one-off probes, production configurations, API control records,
SQLite databases, activation markers and attendance exports do not belong in Git.
The cleanup copied all 72 outstanding files into a protected external archive with
per-file SHA-256 verification before reorganizing branches or removing local SCM
artifacts. Its private `manifest.json` records source paths, hashes, sizes and original
branch/commit. Existing device captures, reconciliation reports, staging directories
and production backups were not moved or modified.

Only reusable source, runbooks and synthetic fixtures are committed. Keep new private
artifacts outside the checkout. Do not use `git clean`, broad staging or a repository
checkout as a production deployment procedure.
