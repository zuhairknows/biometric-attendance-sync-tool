"""Development-only synthetic example generator; no device or Frappe clients exist here."""
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reconciliation_fixtures import bundle, dump, sha, punch, START, END
from scripts import attendance_reconciliation as core


def generate(destination):
    root = Path(destination)
    # An existing fixture set is evidence too: refuse to overwrite it accidentally.
    root.mkdir(parents=True, exist_ok=False)
    lookups = [dict(user_id=user, matches=[dict(employee='SYNTH-EMP-' + user,
        attendance_device_id=user, status='Active')]) for user in ('001', '002', '005')]
    lookups += [dict(user_id='003', matches=[]), dict(user_id='004', matches=[
        dict(employee='SYNTH-EMP-004A', attendance_device_id='004', status='Active'),
        dict(employee='SYNTH-EMP-004B', attendance_device_id='004', status='Active')])]
    records = [[punch(), punch('002', '2026-10-15 07:00:00', code=1),
                punch('003', '2026-10-14 09:00:00'), punch('004', '2026-10-14 10:00:00'),
                punch('005', '2026-10-14 11:00:00'), punch('', '2099-01-01 00:00:00')],
               [punch('005', '2026-10-14 11:00:00', uid=999), punch('006', timestamp='invalid'),
                punch('001', '2026-10-10 12:00:00')]]
    cap, exp = bundle(root, records=records, lookups=lookups)
    captures, source_hashes, _ = core.load_captures(cap, sha(cap))
    contract, mappings, checkins, exports, _ = core.load_exports(exp, sha(exp), START, END, 'Asia/Riyadh')
    rows, summary = core.reconcile(captures, contract, mappings, checkins, START, END, 'Asia/Riyadh')
    detail = root / 'expected-details.csv'
    with detail.open('w', encoding='utf-8', newline='') as stream:
        core.write_details(stream, rows)
    summary.update(inputs=source_hashes + exports, reports=[dict(file=detail.name, sha256=sha(detail))])
    dump(root / 'expected-summary.json', summary)
    dump(root / 'synthetic-trusted-hashes.json', dict(capture_summary_sha256=sha(cap), exports_manifest_sha256=sha(exp)))
    print(json.dumps({'synthetic_example_created': True, 'counts': summary['totals']}))


if __name__ == '__main__':
    generate(sys.argv[1])
