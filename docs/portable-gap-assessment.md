# Release-hardening gap assessment

Assessed before implementation from recovery 3fbf5e967240ac9b164f64567c8a34ff81f959cc.

| Area | Existing implementation | Justified hardening |
|---|---|---|
| Durable synchronization | Schema 1, activation binding, worker mutex, uncertainty/holds/checkpoints | Reuse unchanged; never initialize an existing ledger |
| Recovery | Bounded selection, release/author checks, intents, cancellation, audit/private results | Preserve disabled; existing synthetic regression coverage |
| Dependencies | Accepted 19 pins, verified offline wheels, SpeedFace patch script | Approved wheel package, hash-required lock, exact installed-pin/patch verification |
| Packaging | Source ZIP/bundle, builder tied to recovery branch | Deterministic portable ZIP and version/content identity |
| Installation | install.py invokes unpinned pip and legacy GUI | Explicit preflight, protected version preparation, fresh activation and service stages |
| Upgrade/rollback | Manual guides | Managed receipts, bounded stops/health, current-state backup and compatible-source rollback |
| Manager | Loopback monitor-only CLI, private profile and operation diagnostics | Protected runtime/code with normal-user launcher and fresh private profile |
| Evidence | Offline tests and fixed-host acceptance harness | Portable test matrix; no claim for unexecuted Windows scenarios |

Operator-supplied evidence, October 24, 2026: IT-SUPPORT Phase 2 polled all 12
devices, obtained HTTP 200 delivery, preserved records/schema 1, and the existing
manager worked after relaunch. This certifies neither the new installer nor
historical recovery. FP2_DEVICE_09 monitoring exceptions belong to ordinary
diagnostics; do not alter synchronization rules to mask them.

The monitoring SID must be an ordinary user with approved effective read access.
An administrative identity or configured reader without access fails closed.
None selects the existing strict policy.

No live IT-SUPPORT service, manager, ledger or credentials may be changed.
