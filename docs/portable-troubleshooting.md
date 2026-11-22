# Troubleshooting

| Failure | Required action |
|---|---|
| untrusted_owner / untrusted_write_or_replacement | Use separately approved protected runtime/config; no takeown or ACL weakening |
| wheel_hash_mismatch / installed_pin_mismatch | Stop; obtain independently approved bytes, never bless changed downloads with a new manifest |
| release_hash / installed_source_changed | Preserve evidence, compare against external approved package digest |
| config_requires_literal_review / config_ambiguous | Review dynamic/duplicate configuration independently; never import it to infer settings |
| recovery_or_durable_gate | Durable True and both historical flags False as Python booleans |
| monitor_storage_not_private | Approved ordinary SID needs effective read access and no privileged membership; None selects strict policy |
| critical_startup_or_storage_failure | Read configured absolute logs, including shared legacy logs; SCM Running alone is insufficient |
| health deadline | Verify exact process/token, fresh captures, health and legitimate HTTP 200 delivery; no invented attendance |
| process metadata / mutex / stop timeout | Stop; readable inventory and exclusive host window required; no exclusions/forced worker termination |
| changed core / recovery intents | Independent compatibility/destination review; never restore old DB/drop intents to pass |
| existing directory / partial Prepare | Preserve remnants; separately review ownership/identity/handles. No automatic deletion/reuse |
| service deletion pending | Review management handles; keep all files/data |

Windows venv redirectors may execute their protected base Python child. Verify SCM
command, actual command/identity and venv/base paths; the child image alone is not
proof of wrong hosting.

A fresh install needs explicit activation. An upgrade never provisions an existing
ledger. Backup uses source mode=ro and SQLite backup, then integrity/foreign-key
checks; copy activation while stopped and retain hashes. No raw open-DB copying,
hot-journal removal, immutable/nolock or journal-mode changes.

Network TCP/TLS GET proves transport only. Real service device polling/delivery
proves more. Reboot is never automatic; a separately approved reboot must confirm
delayed-auto startup, actual token, preserved state and no replay.

Additional STOP: elevated_default_owner_requires_review. The installer does not
change the host's default-owner policy. Owner normalization applies only to fresh
installer-created code/runtime trees. Empty owner probes are not employee data.
