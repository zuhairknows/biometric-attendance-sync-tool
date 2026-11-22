"""Explicit, nondestructive import. This module never contacts devices or Frappe."""
import hashlib
import json
from pathlib import Path

from punch_normalization import import_boundary, normalize

MAX_DUMP_BYTES = 64 * 1024 * 1024


def import_dump(ledger, path, device, boundary=None, apply=False, in_codes=(0, 4), out_codes=(1, 5)):
    path = Path(path).resolve()
    report = dict(source=str(path), imported=0, already_known=0, invalid=0, excluded=0, failed=0)
    try:
        # Bound legacy JSON loading. Large files remain intact for an operator-assisted import.
        if path.stat().st_size > MAX_DUMP_BYTES:
            raise ValueError('Legacy dump exceeds 64 MiB import limit')
        raw = path.read_bytes()
        content = json.loads(raw)
        if isinstance(content, dict):
            if content.get('device_id') not in (None, device['device_id']):
                raise ValueError('Legacy dump device ID does not match configuration')
            records = content.get('attendances')
        else:
            records = content
        if not isinstance(records, list):
            raise ValueError('Legacy dump must contain an attendance array')
        digest = hashlib.sha256(raw).hexdigest()
        rows = (normalize(row, device, boundary, in_codes, out_codes) for row in records)
        if apply:
            # Evidence digest and rows commit together. No file is ever modified or deleted.
            report.update(ledger.ingest(rows, device['device_id'], legacy=(str(path), digest)))
        else:
            seen = set()
            for row in rows:
                if row is None:
                    report['excluded'] += 1
                    continue
                known = row['identity_key'] in seen
                if ledger is not None:
                    known = known or ledger.db.execute('SELECT 1 FROM punches WHERE identity_key=?', (row['identity_key'],)).fetchone() is not None
                report['already_known' if known else 'imported'] += 1
                report['invalid'] += int(row['state'] == 'invalid_record')
                seen.add(row['identity_key'])
        report['source_sha256'] = digest
        report['applied'] = apply
    except Exception as exc:
        # Include a safe class/category only; malformed file contents might contain credentials.
        report.update(imported=0, already_known=0, invalid=0, excluded=0, failed=1,
                      error_category=type(exc).__name__, applied=False)
    return report
