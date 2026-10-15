"""Synthetic ONLY. Build fixtures through the actual published capture writer."""
import contextlib
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import capture_device_history_readonly as capture


START = dt.datetime(2026, 10, 11)
END = dt.datetime(2026, 10, 15, 23, 59, 59, 999999)


def punch(user='001', stamp='2026-10-14 08:00:00', uid=1, code=0, **overrides):
    value = dict(user_id=user, timestamp=dt.datetime.fromisoformat(stamp), uid=uid, punch=code, status=1)
    value.update(overrides)
    return value


def dump(path, value):
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')
    return sha(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Terminal:
    def __init__(self, rows):
        self.rows = rows

    def connect(self):
        return self

    def get_attendance(self):
        return self.rows

    def disconnect(self):
        return True


def bundle(root, records=None, lookups=None, checkins=None):
    root = Path(root)
    root.mkdir(exist_ok=True)
    records = records if records is not None else [[punch()], [punch('002', '2026-10-15 17:00:00', code=1)]]
    lookups = lookups if lookups is not None else [
        dict(user_id='001', matches=[dict(employee='SYNTH-EMP-001', attendance_device_id='001', status='Active')]),
        dict(user_id='002', matches=[dict(employee='SYNTH-EMP-002', attendance_device_id='002', status='Active')])]
    checkins = checkins if checkins is not None else [dict(name='SYNTH-CHECKIN-001', employee='SYNTH-EMP-001',
        time='2026-10-14 08:00:00', device_id='OTHER_DEVICE', log_type='IN')]
    args = SimpleNamespace(pause_device=False, start=None, end=None, timeout=10, acl_check=lambda _: None)
    reports = []
    for index, device_id in enumerate(capture.ALLOWED_DEVICES):
        device = capture.Device(device_id, '192.0.2.' + str(index + 1), 4370, 0)
        evidence = capture.record_evidence
        # Keep historical fixture validity independent of the developer host's clock.
        fixed_reference = lambda record, index, reference: evidence(record, index, dt.datetime(2026, 10, 16))
        with mock.patch.object(capture, 'preflight_output', return_value=root), \
                mock.patch.object(capture, 'record_evidence', side_effect=fixed_reference), \
                contextlib.redirect_stderr(io.StringIO()):
            report = capture.capture_one(device, root, args, lambda d, t: Terminal(records[index]), lambda: 'STOPPED')
        assert report['status'] == 'captured', report
        old_path = root / report['evidence_file']
        new_path = root / ('synthetic-device-' + device_id[-2:] + '.json')
        old_path.rename(new_path)
        report['evidence_file'] = new_path.name
        reports.append(report)
    capture_manifest = root / 'synthetic-capture-manifest.json'
    dump(capture_manifest, dict(mode='capture', status='complete', devices=reports,
        all_selected_devices_captured=True, date_filter_affects_summary_only=True))
    mappings = root / 'synthetic-employee-mappings.json'
    dump(mappings, dict(schema='frappe-employee-lookups-v1', lookups=lookups))
    checkin_path = root / 'synthetic-checkins.csv'
    import csv
    with checkin_path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['name', 'employee', 'time', 'device_id', 'log_type'])
        writer.writeheader()
        writer.writerows(checkins)
    export_manifest = root / 'synthetic-export-manifest.json'
    exported_at = max(dt.datetime(2026, 10, 16, tzinfo=dt.timezone(dt.timedelta(hours=3))),
                      *(dt.datetime.fromisoformat(r['capture_finished_at']) + dt.timedelta(seconds=1) for r in reports))
    dump(export_manifest, dict(schema='frappe-offline-reconciliation-v1', site_timezone='Asia/Riyadh',
        exported_at=exported_at.isoformat(), coverage_start=str(START), coverage_end=str(END),
        exports_complete=True, consistent_snapshot=True, checkins_all_devices=True, checkins_all_employees=True,
        mapping_all_statuses=True, server_semantics_verified=True, no_identity_mutating_hooks=True,
        server_source_reference='SYNTHETIC TEST CONTRACT - not a production attestation',
        employee_field='attendance_device_id', mapping_resolution='frappe.db.get_values',
        duplicate_key='employee_time', timestamp_precision='microseconds',
        device_policies={d: dict(punch_direction='AUTO', in_codes=[0, 4], out_codes=[1, 5]) for d in capture.ALLOWED_DEVICES},
        employee_mappings=dict(file=mappings.name, sha256=sha(mappings), rows=len(lookups)),
        employee_checkins=dict(file=checkin_path.name, sha256=sha(checkin_path), rows=len(checkins))))
    return capture_manifest, export_manifest
