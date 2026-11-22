"""Generate one complete synthetic Phase 2A input set for the Phase 2B CLI."""
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_historical_investigation as fixtures
from historical_attendance.recovery import file_hash
from historical_attendance.reporting import write_csv


def generate(destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False)
    fixture = fixtures.InvestigationTests()
    fixture.setUp()
    try:
        fixture.policy['destination_environment'] = 'synthetic'
        contract = root / 'synthetic-contract.json'
        contract.write_text(json.dumps(fixture.policy, indent=2), encoding='utf-8')
        rows, summary = fixture.run_report()
        summary['contract_file_sha256'] = file_hash(contract)
        details = root / 'synthetic-details.csv'
        with details.open('w', encoding='utf-8', newline='') as stream:
            write_csv(stream, rows)
        summary['reports'] = [dict(file=details.name, sha256=file_hash(details))]
        report = root / 'synthetic-summary.json'
        report.write_text(json.dumps(summary, indent=2), encoding='utf-8')
        shutil.copyfile(fixture.path, root / 'synthetic-ledger.sqlite3')
        (root / 'synthetic-destination.json').write_text(json.dumps(dict(synthetic_only=True,
            site_url=fixture.policy['site_url'], employees=fixture.employees, checkins=[]), indent=2), encoding='utf-8')
        print(json.dumps(dict(synthetic_only=True, summary_sha256=file_hash(report), contract_sha256=file_hash(contract))))
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    generate(sys.argv[1])
