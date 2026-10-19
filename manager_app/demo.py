"""Disposable local demonstration. No production credentials, service query or network."""
import datetime as dt
import json
import shutil
from pathlib import Path
import tempfile

from sync_ledger import Ledger
from durable_startup import activation_path, destination_key
from punch_normalization import normalize
from historical_attendance.client import ReadOnlyFrappeClient
from historical_attendance.engine import CLAIMS, investigate
from historical_attendance.repository import SQLiteRepository
from historical_attendance import reporting, recovery
from historical_attendance.recovery_store import RecoveryStore
from historical_attendance.recovery_simulation import SimulationTarget
from scripts import investigate_attendance as cli
from .controller import Controller


class DemoACL:
    """Picklable guard so spawned investigations stay inside the disposable demo."""
    def __init__(self, root):
        self.root = root

    def __call__(self, path, *, service_sid=None):
        if not Path(path).resolve().is_relative_to(self.root):
            raise ValueError('demo_path_outside_sandbox')


def create_demo(*, monitor_only=False, investigation_only=False):
    temporary = tempfile.TemporaryDirectory(prefix='biometric-manager-demo-')
    root = Path(temporary.name).resolve()
    inputs, output, audit, exports = (root / name for name in ('inputs', 'reports', 'audit', 'exports'))
    for directory in (inputs, output, audit, exports):
        directory.mkdir()

    demo_acl = DemoACL(root)

    start, end = dt.datetime(2026, 10, 11), dt.datetime(2026, 10, 11, 23, 59, 59)
    source = inputs / 'synthetic.sqlite3'
    with Ledger(source) as ledger:
        if not monitor_only and not investigation_only:
            # Synthetic activation fixture only. Demo startup must not acquire the
            # production-named mutex or depend on whether the real service is running.
            ledger.db.execute('PRAGMA application_id=12345')
        for device, hour in (('FP1_DEVICE_01', 8), ('FP2_DEVICE_08', 9), ('FP2_DEVICE_11', 10)):
            ledger.ingest([normalize(dict(user_id='100', timestamp=dt.datetime(2026, 10, 11, hour),
                punch=0, status=1, uid=hour), dict(device_id=device, punch_direction=None))], device,
                fetched_at='2026-10-11 11:00:00')
    configuration = inputs / 'synthetic-devices.py'
    configuration.write_text('devices = ' + repr([
        dict(device_id=f'FP{1 if number <= 7 else 2}_DEVICE_{number:02}',
             ip=f'192.0.2.{number}', port=4370 + number, password='DEMO_DEVICE_SECRET')
        for number in range(1, 13)]), encoding='utf-8')
    if monitor_only:
        # Monitoring previews need no investigation report, recovery batch or transport.
        controller = Controller(service_query=lambda: 'RUNNING (demo)', acl_check=demo_acl, sandbox_root=root)
        return temporary, controller, dict(monitor_ledger=str(source), device_config=str(configuration),
            diagnostics_directory=str(output))
    contract = dict(schema='attendance-investigation-contract-v1', destination_environment='synthetic',
        site_url='https://manager.example.test', site_timezone='Asia/Riyadh', timestamp_precision='seconds',
        duplicate_key='employee_time_log_type', coverage_start=str(start), coverage_end=str(end),
        device_ids=['FP1_DEVICE_01', 'FP2_DEVICE_08', 'FP2_DEVICE_11'], evidence_reference='SYNTHETIC DEMO',
        verification={claim: True for claim in (*CLAIMS, 'read_only_api_verified')})
    contract_path = inputs / 'synthetic-contract.json'
    contract_path.write_text(json.dumps(contract), encoding='utf-8')
    scenario = inputs / 'synthetic-destination.json'
    scenario.write_text(json.dumps(dict(synthetic_only=True, site_url=contract['site_url'],
        employees=[dict(name='SYNTHETIC_EMP', attendance_device_id='100', status='Active', modified=str(start))],
        checkins=[])), encoding='utf-8')
    if not investigation_only:
        # Read-only investigation previews do not prepare a recovery batch, even locally.
        target = SimulationTarget(scenario)
        reader = ReadOnlyFrappeClient(target.url, 'DEMO', 'DEMO', target.request)
        rows, summary = investigate(SQLiteRepository(source), reader, start, end, contract['device_ids'], contract)
        summary['contract_file_sha256'] = recovery.file_hash(contract_path)
        detail = inputs / 'synthetic-details.csv'
        with detail.open('w', encoding='utf-8', newline='') as stream:
            reporting.write_csv(stream, rows)
        summary['reports'] = [dict(file=detail.name, sha256=recovery.file_hash(detail))]
        summary_path = inputs / 'synthetic-summary.json'
        summary_path.write_text(json.dumps(summary), encoding='utf-8')
        store = RecoveryStore(audit, acl_check=demo_acl)
        try:
            recovery.prepare(store, source, summary_path, recovery.file_hash(summary_path), contract_path,
                recovery.file_hash(contract_path), receipt_review='SYNTHETIC completed demo reviewed', acl_check=demo_acl)
        finally:
            store.close()

    def demo_cli(args, **options):
        return cli.main(args, request=SimulationTarget(scenario).request, **options)

    controller = Controller(service_query=lambda: 'RUNNING (demo)', acl_check=demo_acl, cli=demo_cli, sandbox_root=root)
    values = dict(monitor_ledger=str(source), backup=str(source), contract=str(contract_path),
        contract_hash=recovery.file_hash(contract_path), output=str(output), workspace=str(audit),
        simulation=str(scenario), normal_lock=str(root / 'synthetic-normal.sqlite3'),
        start=str(start), end=str(end), site=contract['site_url'])
    values['device_config'] = str(configuration)
    values['backup_hash'] = recovery.file_hash(source)
    controller.storage_check = demo_acl
    if not investigation_only:
        service, inbox = root / 'service', root / 'inbox'
        service.mkdir()
        inbox.mkdir()
        live = service / 'synthetic.sqlite3'
        shutil.copyfile(source, live)
        activation_path(live).write_text(json.dumps(dict(schema='durable-activation-v1', ledger_cookie=12345,
            destination_sha256=destination_key(contract['site_url'], 15), cutover_time=str(start),
            review_reference='SYNTHETIC DEMO FIXTURE', provisioned_at='2026-10-10T00:00:00+00:00')), encoding='utf-8')
        values.update(recovery_ledger=str(live), recovery_inbox=str(inbox),
            recovery_summary=str(summary_path), recovery_summary_hash=recovery.file_hash(summary_path))
    return temporary, controller, values
