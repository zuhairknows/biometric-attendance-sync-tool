# One controlled production read-only compatibility verification

Production is `https://erp.myfpf.com`. There is no assumed staging site. Run this
CLI on an isolated review/development Windows PC, never the production sync PC.
Keep the production Windows synchronization service running throughout. This
procedure authorizes no production configuration changes, devices, deployment,
replay or Checkin writes. Execution still requires separate explicit approval.

## What this mode can establish

`--production-read-only` probes counted/paginated Employee equality lookups for
distinct original source IDs and a bounded Checkin time range, then repeats the
reads. It records whether those GETs completed consistently. It never reconciles
production or labels a row potentially missing, even when every contract claim
is true. Valid source rows stay `unable_to_verify`; invalid evidence stays invalid.
Every recovery eligibility/authorization flag remains false.

The existing seven deployment claims are unchanged. Leave unknown claims false,
especially `destination_quiescent_during_scan` while the live sync service is
running. Production mode does not assert or require a fictional quiet staging
deployment. Unverified claims remain in the JSON, and the result is always
`status: unverified`, exit **2**, even if `compatibility_status: reads_complete`.
This is a limited compatibility observation, not full staging/recovery acceptance.

## Mandatory GO / NO-GO prerequisites

- Approved exact feature-branch commit, clean isolated checkout, supported Python,
  `requests`, and no sync/service launcher running in the review checkout.
- Existing consistent, self-contained protected SQLite backup with independently
  recorded SHA256. No writable live ledger, missing WAL, or copy of an actively
  changing database file. Operator read access to inputs; no input modifications.
- Existing approved token, securely supplied, for this production origin. No
  changes to accounts/roles to force a pass. Never use a connector token implicitly.
- Independent deployment review of **read-only API safety**: effective resource
  list handlers and `frappe.client.get_count`, their overrides, authentication and
  middleware must perform no business writes for these GETs. Ordinary server
  access/security logging may still occur. Set `read_only_api_verified: true`
  only with evidence. If this cannot be established, STOP before any API request.
- A five-minute historical interval from the existing backup for `FP1_DEVICE_01`,
  preferably 1-20 available punches and at most three distinct source attendance
  IDs. No new records may be inserted to manufacture a control case. Prefer an
  interval with independently known existing Checkins. The Checkin API scan covers
  ALL destination devices/employees in the interval; it is not filtered to FP1.
- Existing local empty private report directory outside checkout, inputs, config
  and service folders; protected ACLs, no broad grants, at least 256 MiB free.
  Retain the actual Windows ACL validator. No logs with headers, query strings,
  source IDs or response bodies. Protect reports as employee information.
- A named reviewer approves this single origin, range, device, contract hash and
  GET/deadline budgets. No automatic rerun or budget increase after failure.

## Contract: record facts rather than inventing evidence

Copy `docs/phase2a-production-contract.template.json` to private input storage.
Keep `destination_environment: production` and `site_url: https://erp.myfpf.com`.
Set an approved valid local coverage interval and nonempty evidence reference.
The timezone/precision/duplicate-key fields must be explicit; defaults in a
template are not verification. Unknown semantics may be probed only as raw read
compatibility, with the corresponding claims left false and no comparison result.
Review the completed file and record its SHA256 independently.

These claims still need independent deployed evidence; successful GETs/mock tests
do not prove them:

| Claim | Evidence still required |
| --- | --- |
| `employee_lookup_database_equality` | Effective Phase 1 lookup and resource/count filters use the same database equality, including collation, case and trailing-space equivalence; all duplicate/inactive matches retained |
| `unfiltered_employee_and_checkin_visibility` | Actual account roles, user/field permissions, permission query conditions, overrides; authoritative independent matching rows/counts for all relevant devices/statuses |
| `server_duplicate_semantics` | Effective Employee Checkin controller/validation, employee+time(+log-type) rule, device exclusion, explicit NULL versus blank behavior and normalization |
| `source_and_site_timezone_match` | Site timezone and historical device wall-clock interpretation, supported precision/clock transitions; no guessed UTC conversion |
| `historical_payload_policy` | FP1 identity, direction/code settings and saved payload policy for the approved historical interval |
| `destination_quiescent_during_scan` | Independent proof of relevant destination stability; leave false if unknown while the service remains running |
| `no_identity_mutating_hooks` | Effective controller/method overrides, wildcard/Checkin hooks and relevant enabled Server Scripts/constraints |

Review current deployed source references/hashes without invoking submission
methods. Do not call `add_log_based_on_employee_field`, even with invalid data.
Independent visibility evidence may be provided by the site administrator through
an existing read-only process; matching API counts alone are insufficient.

## Commands after explicit execution approval

Prepare the isolated checkout and private directories separately. Replace all
placeholders; the example range must be selected from the actual backup.

```powershell
Set-Location 'C:\Phase2AReview\checkout'
git branch --show-current
git rev-parse HEAD
git status --short
# STOP unless these match the approved feature commit and a clean checkout.

$Ledger = 'C:\Phase2AReview\inputs\ledger-copy.sqlite3'
$Contract = 'C:\Phase2AReview\inputs\production-review.json'
$Output = 'C:\Phase2AReview\reports\production-run-01'
$ApprovedLedgerHash = 'REPLACE_WITH_INDEPENDENT_BACKUP_SHA256'
$ApprovedContractHash = 'REPLACE_WITH_INDEPENDENT_CONTRACT_SHA256'
$Start = 'REPLACE_WITH_APPROVED_LOCAL_START'
$End = 'REPLACE_WITH_APPROVED_LOCAL_END'

$LedgerBefore = (Get-FileHash -LiteralPath $Ledger -Algorithm SHA256).Hash
if ($LedgerBefore -ne $ApprovedLedgerHash) { throw 'Backup hash mismatch' }
if ((Get-FileHash -LiteralPath $Contract -Algorithm SHA256).Hash -ne $ApprovedContractHash) {
    throw 'Contract hash mismatch'
}
if (Get-ChildItem -LiteralPath $Output -Force) { throw 'Output must be empty' }

# Use a fresh PowerShell session without transcription/debug logging.
# Credential username = existing API key; password = existing API secret.
$ApiCredential = Get-Credential -Message 'Approved production read-only API credentials'
try {
    $env:INVESTIGATION_API_KEY = $ApiCredential.UserName
    $env:INVESTIGATION_API_SECRET = $ApiCredential.GetNetworkCredential().Password
    py -B scripts/investigate_attendance.py `
      --ledger-copy $Ledger --start $Start --end $End `
      --device 'FP1_DEVICE_01' `
      --site-url 'https://erp.myfpf.com' `
      --production-read-only --acknowledge-production 'https://erp.myfpf.com' `
      --contract $Contract --contract-sha256 $ApprovedContractHash `
      --max-get-requests 32 --deadline-seconds 120 --request-timeout-seconds 10 `
      --output $Output
    $RunExit = $LASTEXITCODE
}
finally {
    Remove-Item Env:\INVESTIGATION_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:\INVESTIGATION_API_SECRET -ErrorAction SilentlyContinue
    $ApiCredential = $null
}
if ((Get-FileHash -LiteralPath $Ledger -Algorithm SHA256).Hash -ne $LedgerBefore) {
    throw 'STOP: backup changed'
}
if ((Get-FileHash -LiteralPath $Contract -Algorithm SHA256).Hash -ne $ApprovedContractHash) {
    throw 'STOP: contract changed'
}
```

No credentials belong in CLI arguments, JSON, Git, transcripts or shared logs.
No `local_config.py`, activation marker or service configuration is changed.

## Hard limits and STOP conditions

- Default CLI maximum **32 GET attempts**, shared by all count/list requests and
  both passes. Counted before dispatch; failed attempts consume budget. No retries
  or redirects. Configurable `--max-get-requests` range is 1-20,000; retain 32 for
  this approval. No extra prerequisite/live probes are implicitly authorized.
- Default **120-second wall deadline**, configurable 1-600 seconds. After local
  argument/hash/privacy preflight, an isolated worker covers SQLite integrity and
  snapshot reading, API/body reads, comparison/report generation and publication.
  A stalled request cannot leave a background thread issuing more requests: the
  supervisor terminates the worker, with up to two seconds termination allowance.
  Initial local preflight and final supervisor cleanup/receipt are outside the
  investigation budget. Termination cannot undo a GET already received by the server.
- Default connect/read inactivity timeout **10 seconds**, configurable 1-120.
  The separate process deadline also bounds slow/trickling response bodies.
- Only GET `/api/resource/Employee`, `/api/resource/Employee%20Checkin` and
  `/api/method/frappe.client.get_count`. No POST/PUT/PATCH/DELETE, login POST,
  devices, service control or Checkin submission endpoint.

STOP on missing acknowledgement/read-only endpoint evidence, bad privacy/input
checks, exhausted requests, deadline, timeout, HTTP/auth errors, malformed data,
incomplete pagination, changed repeat scans or any unexpected method/endpoint.
Do not fix failures by editing holds, changing permissions, retrying submissions,
stopping production writers or falsely attesting claims.

## Interpret output and unchanged-state checks

For completed compatibility reads, expect terminal receipt
`status: unverified`, `compatibility_status: reads_complete`, and exit **2**.
The private CSV/JSON must agree on scope/counts, include the CSV hash, show no
`potentially_missing`/`already_existing` decisions, and keep every eligibility flag
false. JSON records unverified claims, API attempts, limits and input fingerprints.
`reads_complete` proves only successful stable API observations, not unrestricted
visibility, correct historical semantics or recoverability. Empty source range
produces `not_verified`; it does not demonstrate API compatibility.

Exit **1** (including `investigation_deadline_exceeded`) or **130**, or receipt
`compatibility_status: not_verified`, means STOP. Reports/partial files may remain
if interrupted during publication: even a JSON file is not accepted without the
matching completed terminal receipt. `supervisor_completion_required` is true.
Never treat a timed-out run's artifacts as completed evidence.

Verify backup and contract hashes remain identical. That preserves every source
state, hold and audit record. Verify the checkout is unchanged and reports are
private; verify the CSV SHA256 against JSON. Keep the service running and use
existing approved operational monitoring to confirm no service configuration/state
changes were requested. Do not access the production sync PC as part of this run.

Production Checkins may legitimately change because the live service continues.
Do not promise site-wide before/after equality: use the bounded GET-only request
trace and absence of write endpoints to attribute this tool's behavior. Review
any changes in the chosen historical observations; inconsistent scans fail closed.
Normal server access/authentication logs are not claimed to remain unchanged.

GO only for this limited read-only compatibility probe after endpoint-safety
evidence and explicit execution approval. NO-GO for historical missing-record
conclusions, recovery, deployment or Phase 2B while required claims are unverified.
