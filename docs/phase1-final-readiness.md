# Phase 1 final readiness: two gates, then cutover approval

> Historical procedure: core Phase 1 deployment and integration-account GET checks have completed. Do not rerun the commands below as a current next step. See [current Phase 1 status](phase1-status.md); Auto Attendance remains blocked pending a separately verified safeguard.

Candidate engine: `feature/phase1-service-hardening`, commit
`4256cd6201b17dcda3f9d078552acf1d3c58b3f0`.
Reuse accepted Steps 1–5C2 and the final 14 offline tests. Do not rerun them.
This is a prepared procedure, not authorization to deploy to production.

## 1. One Windows staging acceptance session

The staging-only adapter and single-session runner are now implemented and tested.
Use [the final SCM validation command and criteria](phase1-windows-scm-validation.md)
in administrator PowerShell under the original staging user. No SCM session has
been executed during implementation. The task authorizes only the staging setup
and acceptance, not production service changes or deployment.

The runner validates the existing boundary, prepares a private installed-runtime
copy without package installation, temporarily hosts the unchanged pinned wrapper
under FPFPhase1Staging, checks idle stop/inflight stop/restart of the same queue,
and restores the old stopped probe registration. Preserve all previous evidence;
do not rerun earlier staging tests or use the production wrapper's CLI commands.

SCM PASS requires all 18 checks, one synthetic HTTPS submission, real token and
mutex evidence, bounded clean stops, integrity and pending recovery, unchanged old
hashes, restored original staging registration and a stopped mock/service. Any
failure blocks activation; preserve the new attempt for review. This gate remains
unverified until that actual session passes.

## 2. One administrator-approved, read-only Frappe review

**Access approval:** deployment administrator/Frappe Cloud support provides an
effective deployed-source bundle and authorizes bounded GET checks using the
intended integration account. No Bench availability is assumed. Never invoke
`add_log_based_on_employee_field`, even with synthetic/invalid values.

Send this one evidence request (no credentials or employee details in the reply):

> For myfpf.v.frappe.cloud / erp.myfpf.com, provide current installed app revisions,
> effective Employee Checkin controller and whitelisted insertion-method targets,
> method/controller overrides, wildcard/Employee Checkin document hooks, relevant
> enabled Server Scripts, defaults/constraints and permission-query customizations,
> with relevant deployed source hashes. Confirm exact attendance_device_id lookup,
> duplicate/alias handling, employee+second timestamp+log-type duplicate semantics,
> explicit JSON null normalization, SQL NULL versus empty-string behavior, precision
> and timezone handling. Confirm from deployed middleware/source that 401/403/429
> precede insertion. Provide relevant Shift Type determination settings/current
> watermarks. Use read-only inspection only, without creating documents or changing
> configuration. Identify anything that cannot be verified.

Using the existing approved secure client (credentials must not appear in command
arguments/logs), issue only these standard GET shapes for administrator-selected
existing control identities. No full attendance export is needed:

| GET resource | Fields | Filters | Limit |
| --- | --- | --- | --- |
| `/api/resource/Employee` | `name,status,attendance_device_id` | `attendance_device_id = <control biometric User ID>` | 2 |
| `/api/resource/Employee%20Checkin` | `name,employee,time,log_type` | `employee = <resolved employee>`, `time = <exact second>`, `log_type = ""` | 2 |

Do not filter by device or change the account/roles to force a pass. An
administrator must independently compare authoritative counts/matching rows with
these GET results, including known controls that expose user-permission filtering
or ambiguity and relevant blank/NULL/all-device cases. Retain only sanitized
counts/hashes in the shared evidence. Two rows detect ambiguity, not permission
completeness. A connector result or successful GET alone does not certify either
unfiltered access or write permission. Source/middleware review supplies insertion
semantics; no live insertion test is authorized.

**Frappe PASS:** effective deployed behavior supports the pinned client's actual
mapping, null/blank, timestamp, duplicate and refusal handling; exact confirmation
GETs are unfiltered for the intended account; relevant blank-log-type attendance
effects are understood. Any unavailable runtime hooks/source or permission proof
remains unverified and blocks activation. Do not set production confirmation flags
true based on synthetic staging evidence or dashboard app revisions alone.

## 3. One final cutover decision, no execution

Record this short decision sheet after both gates pass:

- Pin the approved release artifact/hash, unchanged production service identity,
  endpoint, configuration/direction policy, interpreter/SDK and local/site clocks.
- Set an explicitly approved future local cutover second. Live eligibility is
  at/after the later of that boundary and IMPORT_START_DATE. Account for the gap
  from the old worker's last verified capture through activation; do not backdate
  the boundary or call the gap recovered.
- Preserve historical punches as `cutover_hold`, with unchanged source/payload;
  no deletion, delivered marking, automatic release or recovery is authorized.
  Approve their potentially indefinite cap on Shift Type watermarks and the
  interaction with existing remote/local watermark floors. If attendance requires
  advancement incompatible with those holds, this policy remains an activation
  blocker; do not bypass it by editing ledger or watermark values.
- Confirm protected local NTFS storage, space/critical-event monitoring and owner,
  a current SQLite backup plus its activation marker, integrity/hash verification,
  and an interval accounting owner. Matching activation cookies cannot detect a
  stale backup. Never overwrite the latest queue with an old backup or new empty DB.
- Rollback defaults to a stopped sender with the latest ledger/marker preserved,
  or an approved durable-compatible repair. Legacy rollback requires every
  SQLite-only unresolved record to be accounted for first; retain code/config
  backups and original dumps. Do not automatically resume legacy processing.

**READY FOR A SEPARATE DEPLOYMENT APPROVAL** only when the SCM and Frappe gates
pass and every decision-sheet item is resolved. Otherwise BLOCKED, with the exact
missing item named. This procedure performs no production installation, service
change, submission, watermark update or historical recovery. Stop here.
