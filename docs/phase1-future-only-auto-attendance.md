# Future-only Auto Attendance safeguard: design only

This is the design discussed on October 17, 2026, preserved during repository
cleanup. No server safeguard has been implemented or deployed. Neither this design
nor the existing watermark correction authorizes historical Attendance changes.

## Minimum intended policy

Choose a fixed, explicitly approved earliest Attendance date `D` in the site
timezone. It must be a fully captured future shift date, not the partial biometric
activation day or a moving value computed from today's date. Eligible shifts must
have complete capture windows after the approved biometric activation boundary.

The narrow proposed change is a Shift Type controller extension, retaining normal
HRMS calculations, with three date restrictions:

1. Filter Checkin groups by **shift-start Attendance date >= D** before creating or
   updating Attendance, linking Checkins, marking them skipped, or adding comments.
2. Clamp absence processing to `max(existing_start_date, D)`; an empty date range
   must produce no Attendance.
3. Restrict existing half-day completion to **Attendance date >= D** before its
   direct database update. Historical half-day rows remain untouched.

Overnight shifts use their shift-start Attendance date, not Checkin/shift-end dates.
Preserve early-arrival buffers, assignments, holidays and normal absence delays.

## Why configuration or an Attendance validation script is insufficient

At operator-pinned HRMS `6091df8`, `ShiftType._process` derives Attendance dates
from `shift_start`. Checkin selection uses `process_attendance_after` as a punch-time
lower bound and `last_sync_of_checkin` as an upper shift-completion bound. Absence
processing derives another date range, while `mark_absent_for_half_day_dates`
selects flagged historical half-day rows without a processing-start lower bound.

Both the Checkin-based half-day path and the missing-punch half-day path use
`frappe.db.set_value`. An Attendance document validation script cannot reliably
guard those direct updates. A date-setting change alone does not close every path.

Source references:

- [Shift Type processing](https://github.com/frappe/hrms/blob/6091df8/hrms/hr/doctype/shift_type/shift_type.py)
- [Checkin attendance creation and half-day updates](https://github.com/frappe/hrms/blob/6091df8/hrms/hr/doctype/employee_checkin/employee_checkin.py)

`process_attendance_after` remains an additional lower bound; raising it to `D`
can exclude early-arrival punches for eligible shifts. `last_sync_of_checkin`
remains the upper completeness bound and must never override the date safeguard.

## Hosting and activation prerequisites

The Cloud owner/deployment administrator must first confirm a supported route for
a version-controlled controller extension, preferably through an existing managed
custom app. Custom apps on Frappe Cloud require a private bench; do not assume this
site has one or that Server Scripts/custom hooks are available. If no deployment
route exists, keep the watermark correction undeployed and request hosting support.

Required before activation: approved `D`, pinned-version focused validation of
creation, existing updates, absences, half-days and overnight shifts, plus proof
that effective controllers, scheduled/manual entry points and queued jobs use the
guard. Public repository source is not runtime verification of site customizations.

Only after the server safeguard is verified may a separately authorized deployment
activate the [cutover-aware watermark correction](phase1-watermark-correction.md).
Keep all historical holds, unresolved-record blockers, failed-scan blockers,
watermark floors and monotonicity. Do not remove the server safeguard while advanced
watermarks could expose history. No manual watermark advancement or replay is part
of this design.
