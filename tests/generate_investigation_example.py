"""Generate synthetic Phase 2A reports with real SQLite and mocked ERPNext GETs."""
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_historical_investigation import InvestigationTests
from historical_attendance.reporting import write_csv
from sync_ledger import Ledger


def generate(destination):
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=False)
    fixture = InvestigationTests()
    fixture.setUp()
    try:
        fixture.checkin()
        fixture.add(user_id='100', timestamp='2026-10-11 09:00:00', uid=2)
        fixture.add(user_id='NO_MAPPING', uid=3)
        fixture.add(user_id='DUPLICATE_MAPPING', uid=4)
        for suffix in ('A', 'B'):
            fixture.employees.append(dict(fixture.employees[0], name='SYNTHETIC_DUP_' + suffix,
                                          attendance_device_id='DUPLICATE_MAPPING'))
        fixture.add(user_id='100', timestamp='2026-10-11 10:00:00', uid=5)
        fixture.add('TEST_B', user_id='100', timestamp='2026-10-11 10:00:00', uid=6)
        fixture.add(timestamp='invalid', uid=7)
        with Ledger(fixture.path) as ledger:
            ledger.db.execute("UPDATE punches SET state='rejected', error_category='cutover_hold' WHERE id=2")
            ledger.db.commit()
        rows, summary = fixture.run_report()
        summary['synthetic_example_only'] = True
        detail = target / 'sample-details.csv'
        with detail.open('w', encoding='utf-8', newline='') as stream:
            write_csv(stream, rows)
        summary['reports'] = [dict(file=detail.name, sha256=hashlib.sha256(detail.read_bytes()).hexdigest())]
        (target / 'sample-summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
        print(json.dumps(summary['totals']))
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    generate(sys.argv[1])
