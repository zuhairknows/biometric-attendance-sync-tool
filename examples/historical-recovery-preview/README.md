# Synthetic recovery preview examples

All inputs and employees here are synthetic. No credentials or production records are
included. Test attestations deliberately say `SYNTHETIC ONLY`; they prove program behavior,
not deployed server behavior. Never reuse these verification flags for real evidence.

- `synthetic-preview.csv` and `synthetic-preview-summary.json`: one candidate eligible
  **for review**, one already matched through another device, one unmatched employee,
  two duplicate-source punches across devices, one ambiguous employee mapping and one
  missing punch outside the approved review scope.
- `synthetic-blocked-preview.csv` and `synthetic-blocked-summary.json`: same evidence,
  with verification claims absent. The candidate becomes blocked; zero eligible records.
- `inputs/`: complete miniature capture/export/baseline/review contracts and explicit
  SQL log-type storage evidence. The input review artifacts select source anchors only;
  they are not endpoint requests or recovery authorization.

The example snapshot/review times are fixed to October 16, 2026 for reproducible tests.
The real CLI enforces freshness using the current clock and has no clock/age override.
Therefore these archived examples will intentionally fail freshness checks outside their
15-minute window. Automated tests replay them with an injected synthetic clock; do not
alter a review-PC clock or falsify timestamps to run old exports as fresh data.

Reports are public here only because every record is synthetic. Real previews must use a
private local output directory outside the repository and original evidence. See the
[operator and safety runbook](../../docs/historical-attendance-recovery-preview.md).
