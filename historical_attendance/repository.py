"""Read a consistent ledger snapshot without invoking any queue operations."""
from sync_ledger import Ledger
from .source import verified_source


class SQLiteRepository:
    def __init__(self, path):
        self.path = path

    def read(self, start, end, devices, cancelled=lambda: False):
        rows = []
        # Reuse Phase 1 schema/integrity checks and its actual mode=ro connection.
        with Ledger(self.path, readonly=True) as ledger:
            ledger.db.execute('PRAGMA query_only=ON')
            ledger.db.execute('BEGIN')
            ledger.verify_integrity()
            for device in devices:
                for record in ledger.db.execute('SELECT * FROM punches WHERE device_id=? ORDER BY id', (device,)):
                    if cancelled():
                        raise KeyboardInterrupt
                    row = dict(record)
                    try:
                        stamp, _ = verified_source(row)
                    except (KeyError, ValueError, TypeError, OverflowError):
                        # Unknown timestamps cannot safely be excluded by a date filter.
                        row['range_status'] = 'unknown'
                    else:
                        if not start <= stamp <= end:
                            continue
                        row['range_status'] = 'within'
                    rows.append(row)
        return rows
