# Upgrade, migration and rollback

Never install over the working application. Preserve both production folders and
monitoring profile. Prepare a separate version; keep current ledger/activation paths.

## Managed upgrade

Automated upgrades require trusted managed receipts, unchanged core application
source and byte-identical private config. Changed synchronization/recovery code needs
new compatibility review, not an override. Unknown registration, config drift,
changed core hashes, intents or inaccessible inventory is NO-GO.

Prepare the next version using the existing protected config and data root. Do not
Activate. The original service can keep running during Prepare.

~~~powershell
& $Driver -Stage Upgrade -Receipt '<new receipt>' -PreviousReceipt '<old receipt>' -Approve -ExclusiveHostWindow -CheckNetwork
~~~

The tool performs read-only network checks before stop, validates state, stops the
recognized worker with bounded waits, confirms process/mutex quiescence, creates
and verifies a SQLite backup plus activation copy, rechecks intents, then switches
only ImagePath. Record account/startup/dependencies/failure actions before/after.

On startup/health failure, it stops the new worker, revalidates current state,
restores the compatible old command and verifies old-service health. It never
restores a backup. If rollback validation/stop/switch fails, preserve evidence and
leave unsafe writers stopped for review.

~~~powershell
& $Driver -Stage Rollback -Receipt '<installed new receipt>' -Approve -ExclusiveHostWindow -CheckNetwork
~~~

Later rollback preserves current captures, acknowledgements, retries, leases,
holds/checkpoints and activation. Do not reset pending rows, drop sr_intents,
delete journals or restore an old database. Unknown remote outcomes require
independent destination confirmation.

## Legacy installations, including IT-SUPPORT

An existing service without trusted managed receipts is deliberately not adopted.
Install refuses the collision. A user-owned runtime, unknown source or untracked
rollback script cannot be certified by this driver.

Separately reviewed migration:
1. Inventory actual SCM command/account/startup/failure actions, process token,
   source revision, runtime/pins/patch, private config, ledger/activation, ACLs,
   ancestors and manager/profile. Preserve both existing application folders.
2. Confirm exact old/new schema, activation, uncertainty rules, intent fencing and
   actual LocalSystem storage compatibility. A monitoring SID must match approved
   ordinary-user read access; None selects SYSTEM/Administrators-only validation.
3. Prepare a protected side-by-side release. Keep credentials/device mappings,
   cutover and current state unchanged.
4. Review and test the bounded stop/ImagePath/current-ledger rollback procedure
   in historical-recovery-deployment.md. No fabricated receipt or bypass flag.
5. Authorized maintenance only: stop/confirm worker exit, take final verified backup
   and activation pair, switch/start once, require real health and all-device polling/
   legitimate delivery. Roll back only to a known compatible sender with current state.

No production migration occurred. Arbitrary-version upgrade/adoption remains
unsupported until independently reviewed and tested.
