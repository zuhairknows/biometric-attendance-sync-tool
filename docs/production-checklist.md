# Production Verification Checklist

Use this checklist before and after the first controlled Windows service installation.

## Repository

- [ ] Running from clean `master` before creating a deployment branch
- [ ] Tests passing
- [ ] No secrets tracked
- [ ] `local_config.py` is ignored by Git
- [ ] `local_config.py` is not tracked by Git
- [ ] No logs, `status.json`, retry dumps, `.venv`, or cache files tracked

## ERPNext

- [ ] API user is active
- [ ] API user can create Employee Checkin
- [ ] API user can write Shift Type if last-sync updates are used
- [ ] Employee `attendance_device_id` matches ZKTeco PIN/User ID
- [ ] Shift Assignments or default shifts are correct
- [ ] Auto Attendance configuration is verified
- [ ] Go-live `IMPORT_START_DATE` is correct

## Device

- [ ] Device is reachable from the Windows integration PC
- [ ] Correct IP address or hostname
- [ ] Correct port
- [ ] Correct connection password
- [ ] Permanent logical `device_id` is correct
- [ ] Device clock and timezone are correct
- [ ] `clear_from_device_on_fetch = False`

## Functional Tests

- [ ] Normal punch creates Employee Checkin
- [ ] Duplicate punch is idempotent
- [ ] Unknown employee does not stop the entire service
- [ ] Inactive employee behavior is verified
- [ ] Device offline does not stop another device
- [ ] Temporary ERPNext outage is logged and recovers on a later cycle
- [ ] Retry dump survives restart
- [ ] Arabic logs render correctly
- [ ] `status.json` survives restart

## Windows Service

- [ ] Service installs
- [ ] Service starts
- [ ] Service stops
- [ ] Service restarts
- [ ] PC reboot tested
- [ ] Service automatically starts after reboot
- [ ] Service runs while no interactive user is logged in
- [ ] Logs are still written
- [ ] ERPNext receives new checkins
- [ ] Service recovery is configured
