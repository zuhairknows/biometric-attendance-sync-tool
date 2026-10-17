# Cutover-aware watermark correction — development only

No deployment, service restart, Frappe request or production write was performed.
Changes are limited to `sync_ledger.py`, `durable_sync.py` and focused tests. No
schema, ledger state, delivery/retry policy, device polling or timestamp changes.

## Rule

`update_shifts` validates the existing activation marker/destination and uses the
later of its cutover and `IMPORT_START_DATE`. `safe_watermark(..., cutover=...)`
excludes only `state=rejected`, `error_category=cutover_hold` rows with canonical,
valid naive timestamps strictly before that boundary. The calculation performs
SELECTs only. Hold states, source evidence and payloads are never changed/replayed.

All remaining unresolved states, including post-cutover holds, unknown/malformed
timestamps and failed/in-progress scans still block or cap progress. Existing
local and legacy floors, minimum across mapped devices and second rounding remain.
Calls without a cutover retain conservative historical blocking. Distinct timestamp/
state/category tuples reduce repeated parsing without introducing a cache/schema.

## Read-only backup comparison

Input: operator-transferred `sync.sqlite3` and `sync.sqlite3.activation.json` in
`Documents\FPF-Phase1-Watermark-Review`; production backup reference ends
`post-activation-717bbaeafa534a309d2f4d4280c9df92`.

- Marker schema/cookie matches the copied database; opened with `mode=ro` and
  `query_only=ON`. Database and marker SHA-256 values matched before/after review.
- Database SHA-256: `5947af1f9835dab60d0d506491aa19c617d5beed2cd2662f845ec995310e3181`.
- Operator confirms `IMPORT_START_DATE=20260930`; effective boundary is
  **2026-10-17 08:58:22**, production local time (UTC+03:00).
- Snapshot contains 270,257 held records, 482 synced, 2 retry and 48 unmatched.
  These counts describe that backup, not the live service now. No excluded biometric
  IDs were investigated or changed.
- All 21 saved ledger shift floors are **2025-05-26 14:56:56**.

Calculated caps (all corrected dates are 2026-10-17):

| Device | Old cap | Corrected cap | Remaining constraint |
|---|---|---|---|
| FP1_DEVICE_01 | 2025-05-26 14:56:56 | 12:07:27 | Last successful fetch |
| FP1_DEVICE_02 | 2026-08-13 12:46:57 | 11:42:42 | Post-cutover quarantine |
| FP1_DEVICE_03 | 2026-08-13 12:39:24 | 11:45:25 | Post-cutover quarantine |
| FP1_DEVICE_04 | 2026-08-13 12:40:30 | 10:04:09 | Uncertain submission |
| FP1_DEVICE_05 | 2026-08-13 12:49:27 | 12:07:57 | Last successful fetch |
| FP1_DEVICE_06 | Blocked | Blocked | `fetch_ok=0` |
| FP1_DEVICE_07 | 2026-08-13 12:38:14 | 10:33:51 | Post-cutover quarantine |
| FP2_DEVICE_08 | 2026-08-13 12:37:43 | 09:08:16 | Post-cutover quarantine |
| FP2_DEVICE_09 | 2026-08-13 12:51:59 | 09:12:05 | Earliest post-cutover quarantine |
| FP2_DEVICE_10 | 2026-08-13 12:46:45 | 09:05:11 | Post-cutover quarantine |
| FP2_DEVICE_11 | 2026-08-13 12:41:10 | 09:05:03 | Post-cutover quarantine |
| FP2_DEVICE_12 | 2026-08-13 16:38:38 | 12:02:26 | Last successful fetch |

FP1_DEVICE_06's last fetch attempt began at 12:08:00.914249; its last successful
fetch was 12:00:33.791210. `fetch_ok=0` can mean an in-progress scan in this online
snapshot, not necessarily a persistent device failure. Both old/new calculations
refuse progress for that device. Do not mark it successful manually.

The combined all-12-device cap therefore remains blocked in this exact snapshot.
The FP2-only cap changes from 2026-08-13 12:37:43 to 2026-10-17 09:05:03.
If FP1_DEVICE_06 subsequently completes a successful fresh scan with no other
state changes, the all-device minimum would be 09:05:03, still capped by unresolved
post-cutover work. This is conditional, not a change applied to the backup.

The operator subsequently confirmed from the actual production configuration
inventory: one mapping, all 21 Shift Types and all 12 devices. Applying that shared
mapping to this backup gives **no candidate under either calculation** because
FP1_DEVICE_06 has `fetch_ok=0`. Therefore all 21 watermarks remain at their existing
2025-05-26 14:56:56 floors in this snapshot. There are no independent per-shift
advances: one mapped device's unavailable scan blocks every shift in the shared map.

Once all mapped scans succeed, with other snapshot state unchanged, the corrected
shared cap would be **2026-10-17 09:05:03** for all 21 shifts, limited by the earliest
post-cutover quarantine. This is conditional; no scan flags or watermarks were edited.
The mapping confirmation closes the earlier per-shift projection uncertainty, not
the historical Auto Attendance risk below. Both backup-file hashes remained unchanged.

## Historical Auto Attendance risk — deployment not proposed

Reviewed public HRMS source at the operator-pinned commit **6091df8**:
[shift_type.py](https://github.com/frappe/hrms/blob/6091df8/hrms/hr/doctype/shift_type/shift_type.py).
This is committed-source behavior, not certification of deployed overrides/jobs.

- `get_employee_checkins` selects unlinked, non-skipped, non-offshift Checkins with
  time at/after `process_attendance_after` and shift end before the watermark. It
  has no Windows-ledger cutover predicate.
- `_process` marks/links qualifying logs and also attempts absence marking.
  `get_start_and_end_dates` can include historical dates from the processing start
  (or later joining date), subject to assignment/holiday/existing-attendance rules.
- `mark_absent_for_half_day_dates` can update existing flagged Half Day Attendance
  up to the watermark; its initial query has no processing-start lower bound.

Previously read site settings for the 21 enabled business shifts have processing
starts in September 2026 and old May 2025 watermarks. Moving the watermark into
October can expose existing historical Checkins, create historical Attendance/
absences, and update eligible existing half-day records **without punch replay**.
Exact affected counts require Attendance, Checkin eligibility and assignment data
not present in this SQLite backup. No production source hooks or scheduler were
invoked to test the risk.

Under the present requirement not to change historical Attendance, this engine
correction alone does not establish deployment safety. Historical processing must
be explicitly assessed/authorized or prevented through a separately reviewed server
policy before deployment. No Shift Type/Attendance changes are included here.

## Focused verification

10 new tests cover strict boundaries/microseconds, immutable history, malformed/
unknown timestamps, wrong labels/states, all unresolved post-cutover states, failed
or missing devices, device minima, invalid/missing activation and floor monotonicity.
Existing affected suites: 47 durable-engine tests and 24 service-hardening tests.
These are offline/synthetic; no full staging rerun or production test was performed.
# Follow-up branch status

Preserved on `feature/phase1-auto-attendance` during Phase 1 repository cleanup.
This correction is **not deployed** and is excluded from `release/phase1`.
The future-only server Attendance safeguard is design-only and has not been implemented.
Do not activate this correction until that safeguard is deployed and verified under
separate authorization. The deployed runtime remains commit `4256cd6`.

