# Frappe Cloud Employee Checkin: deployed-source verification plan

> Historical procedure: core Phase 1 deployment and integration-account GET checks have completed. Do not rerun the commands below as a current next step. See [current Phase 1 status](phase1-status.md); Auto Attendance remains blocked pending a separately verified safeguard.

Prepared October 15, 2026. Planning only; no server collection or execution performed.
The accepted reconciliation baseline, original inputs and earlier reports stay unchanged.

## Scope and starting evidence

The operator confirms `erp.myfpf.com` and `myfpf.v.frappe.cloud` are the same site.
The reviewed Windows integration at `a23b8112fd1b87f3d2bb10f798b45e150aadcd7a`
has literal None direction for both affected devices in the operator's read-only
configuration diagnostic. Its request includes an explicit JSON `"log_type": null`
and targets:

`hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field`

This establishes the source/on-disk request candidate, not the service's retained
memory or the server's handling. None must not be replaced with AUTO, IN, OUT, an
omitted argument or a blank string when reviewing the server path.

The missing evidence is the actual deployed server implementation. Repository tags,
reported app versions, upstream source and ordinary Desk metadata cannot substitute
for it. Current deployment evidence also does not establish incident-time code.

## Collection route: administrator chooses an existing safe mechanism

1. Ask Frappe Cloud support or the deployment administrator for read-only inspection
   of the exact running deployment, or a private source bundle bound to it. Do not
   assume tenant Bench/SSH access is available. A provider-controlled deployment
   artifact is acceptable if its relationship to the running workers is attested.
2. Prefer filesystem/deployment-artifact reads and existing administrator-visible
   runtime/metadata inventories. Do not initialize Frappe, import application modules
   or invoke Bench console/execute just to collect this evidence: startup, cache and
   framework lifecycle behavior can have side effects. Any runtime inventory must
   use an existing administrator-approved mechanism known to be non-mutating.
   If no such mechanism exists, record the inventory as unavailable.
3. Bind evidence to the exact site and deployment/image/release ID, collection time
   with timezone, app commits/order, local patches and running-worker consistency.
   Distinguish checked-out files from loaded code. Capture deployment identity and
   file hashes at collection start/end; if they differ, reject the mixed bundle.
   This does not authorize a deployment freeze, restart or configuration change.
4. Collect only the source and non-personal metadata below. No database credentials,
   API tokens, employee records, logs, request captures or full site configuration
   are required. No external database connection/account provisioning is requested.
5. Store the bundle in an administrator-controlled private location, outside public
   web assets, and transfer it through an approved private channel. Record SHA-256
   hashes independently. Supply relevant source paths and line ranges in findings.
   If source embeds secrets, provide reviewed redacted excerpts with separate
   redacted-file hashes and an administrator attestation binding them to the original
   source hashes. Redacted or omitted decision logic remains unresolved.

No Employee Checkin insertion endpoint call, test document, synthetic write followed
by rollback, request replay, new Server Script/API, File document, package install,
service/device operation or production data/configuration change is part of this plan.

## Minimal evidence and questions to resolve

Start with the resolved endpoint/controller module and effective hook subset.
Follow only helpers that affect these decisions; do not request an entire Bench.
File paths below are navigation hints, not assertions about the deployed layout.

| Question | Minimum deployed evidence | Required finding |
|---|---|---|
| Explicit JSON null | Actual request JSON/argument binding code, resolved endpoint signature/body and decorators/overrides | Whether null reaches Python as None, is dropped, defaulted, coerced or rejected; distinguish explicit null from omitted and empty-string arguments |
| Resulting log_type | Endpoint assignment, actual controller/base lifecycle, field defaults/metadata, relevant validation and persistence conversion | Value at construction, before duplicate checking and at persistence: SQL NULL, empty string, derived IN/OUT or rejection; give every relevant condition and code order |
| Duplicate identity | All duplicate validators, inherited/custom paths, called existence/query code and relevant indexes/constraints/triggers | Exact fields/operators, self exclusion, device participation, validation timing, and how None/empty string become actual query predicates; do not assume SQL NULL equals empty string |
| Employee resolution | Endpoint's actual employee_fieldname default, lookup call/helpers and applicable override code | Lookup field and filters, database matching/collation/type coercion, leading-zero handling, status filtering/validation and zero/multiple-match behavior |
| Timestamp handling | Called parser/timezone helpers, controller before-validation, DB serialization and time-column definition | Accepted representation, site-local versus UTC/offset conversion, naive handling, precision/truncation/rounding, and value used at duplicate check versus storage |
| Effective custom behavior | Site-bound app order, resolved method/controller, effective hook subset and relevant enabled script/constraint inventory | All hooks/overrides that can affect employee, time, log_type, duplicate checks, rejection or persistence, including indirect calls and wildcard events |

Likely source entry points to locate in the actual deployment:

- The resolved HRMS/ERPNext Employee Checkin module, plus any overriding or inherited
  controller, endpoint or helper actually used. Whole relevant modules are preferable
  to snippets that could hide branches or decorators.
- The deployed Frappe request dispatcher/JSON-to-argument binding helper;
  `frappe/model/document.py` or the actual base lifecycle implementation;
  called datetime helpers (often `frappe/utils/data.py`); DB serialization and query
  implementations (often `frappe/database/database.py` and `frappe/database/query.py`).
  Include another helper only when the traced path depends on it.
- The effective site entries for `override_whitelisted_methods` at the exact endpoint,
  `override_doctype_class` / `extend_doctype_class` for Employee Checkin, `doc_events`
  for Employee Checkin **and `*`**, plus relevant request/global hooks or patches.
  Include referenced functions, applicable dynamic/conditional hook resolution and
  relevant `hooks.py` declarations. A static grep or empty per-DocType list alone
  cannot establish absence of effective custom behavior.
- Relevant enabled Server Scripts, API-name collisions and delegated validations,
  including whether scripts actually execute on this site. A permission-filtered
  inventory cannot certify completeness. Include relevant custom-field/property
  defaults, unique indexes and database triggers; do not export employee rows.
- Only non-secret context: deployed site timezone; relevant DB session timezone/SQL
  mode; time-column type/precision; log_type nullability/default; lookup column
  collation. Metadata reads, if needed, must use an existing approved read-only
  administrator mechanism. No direct database connectivity from this workstation
  is required. If metadata cannot be read safely, mark its conclusion unresolved.

The administrator should explain whether otherwise-valid requests with explicit
null are accepted and what source-derived value would persist. Do not claim that
a record was observed being inserted. If a conditional depends on unknown data,
report the condition and unresolved result rather than creating data to exercise it.
Shift/Auto Attendance processing and insertion-time direction handling must be
distinguished; a template comment is not proof that one derives the other's value.

## Review procedure and acceptance criteria

1. Check deployment binding and SHA-256 evidence before interpreting code. Confirm
   all serving workers use the supplied code, or identify unresolved worker drift.
2. Trace a conceptual input with explicit log_type null through request binding,
   employee resolution, document defaults, hooks, timestamp normalization, duplicate
   queries and persistence. This is source reasoning, not endpoint execution.
3. Produce one short decision record per question: conclusion, conditions, source
   path/lines, file hash, deployment ID, metadata dependencies and status:
   **verified deployed source**, **conditional source-derived outcome**, or
   **unresolved**. Distinguish source proof from live runtime/persistence observation.
4. For duplicate handling, explicitly state the predicates for None and empty string.
   The reconciliation CSV normalizes missing log types to blank; it cannot establish
   that database NULL and blank are interchangeable. If this distinction matters,
   identify the required export/reconciliation contract review; do not change it here.
5. Stop certification for unbound source, missing helper branches, incomplete hooks,
   unsupported custom identity logic or uncertain normalization. Keep existing
   verification-template flags unasserted. This investigation alone does not certify
   export completeness, Windows memory, snapshot provenance or historical continuity.
6. The existing verifier deliberately rejects some overrides/hooks/source shapes.
   Document unsupported behavior as a blocker. Do not omit hooks, toggle attestations
   or weaken code to make it pass. No reconciliation or manifest changes are included.
7. Report implications separately. Preserve October 14-15 counts and all existing
   reports; any future re-evaluation requires a new authorized scope and output
   location. Recovery remains unauthorized even after source questions are resolved.

## Copy-ready minimal request to support or the deployment administrator

> Subject: Read-only deployed Employee Checkin source verification — myfpf.v.frappe.cloud
>
> Please provide administrator-approved read-only source findings or a private source
> bundle from the exact running deployment for myfpf.v.frappe.cloud (erp.myfpf.com).
> Our reviewed Windows client targets
> hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field
> and includes explicit JSON `"log_type": null` for both affected devices.
>
> Minimum requested evidence:
> 1. The actual resolved endpoint/Employee Checkin controller and only the request,
>    defaulting/persistence, duplicate-query, employee-lookup and datetime helper
>    source needed to explain: explicit null handling; stored log_type (NULL, blank,
>    derived or rejection); exact duplicate fields and NULL/blank predicates; default
>    employee lookup field; timestamp precision and timezone conversion.
> 2. The site-effective relevant method/controller overrides, Employee Checkin and
>    wildcard hooks, enabled Server Scripts/custom validators and relevant field
>    defaults/constraints. Include the applicable app order and referenced code;
>    identify any part of the effective inventory you cannot verify read-only.
> 3. Deployment/image ID, running-worker/source binding, app commits/local patches,
>    collection time, file SHA-256 hashes and only relevant non-secret schema/timezone
>    metadata. Please distinguish current evidence from incident-time evidence.
>
> A concise source-cited answer is acceptable if source files cannot be shared;
> include file hashes/line references and explicitly mark unverified branches.
> Please identify a safe available collection mechanism rather than assuming tenant
> Bench access. Do not call the insertion endpoint, create test Checkins, replay
> requests, import/init application code for probing, install anything, add an API/
> Server Script, alter configuration/data or restart services. No credentials,
> employee data, full site configuration or external database access are requested.
> If safe deployed-source or runtime-inventory access is unavailable, please state
> precisely which evidence cannot be supplied. Upstream source/version numbers alone
> will not establish deployed behavior.

This is a prepared request only. It has not been sent to support or any administrator.
