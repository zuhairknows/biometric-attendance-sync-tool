"""Read-only diagnostics, SQLite backup, and explicitly applied legacy dump import."""
import argparse
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

from legacy_import import import_dump
from punch_normalization import import_boundary
from sync_ledger import Ledger


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='Explicit absolute database path; never inferred from production config')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='Open an existing database read-only; print aggregate counts only')
    sub.add_parser('verify', help='Read-only full SQLite integrity and foreign-key checks')
    backup = sub.add_parser('backup', help='Use SQLite online backup; source is opened read-only')
    backup.add_argument('--output', required=True)
    migration = sub.add_parser('import-legacy', help='Preview by default; source dumps always retained')
    migration.add_argument('--config', required=True, help='Trusted local Python config containing original device mapping')
    migration.add_argument('--directory', required=True)
    migration.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    path = Path(args.db)
    if not path.is_absolute():
        parser.error('--db must be absolute')
    try:
        if args.command in ('status', 'verify'):
            with Ledger(path, readonly=True) as ledger:
                print(json.dumps(ledger.diagnostics() if args.command == 'status' else ledger.verify_integrity(), indent=2))
        elif args.command == 'backup':
            output = Path(args.output)
            if not output.is_absolute() or output.exists() or output.resolve() == path.resolve():
                raise ValueError('Backup output must be an absolute, new file path')
            with Ledger(path, readonly=True) as ledger:
                # A sqlite3 connection's own context manager commits but does not close it.
                # Explicit close matters on Windows, where an open handle prevents file moves.
                with closing(sqlite3.connect(str(output))) as target:
                    ledger.db.backup(target)
                    if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise RuntimeError('Backup integrity check failed')
            print(json.dumps({'backup': str(output), 'integrity': 'ok'}))
        else:
            spec = importlib.util.spec_from_file_location('legacy_migration_config', args.config)
            config = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(config)
            if not Path(args.directory).is_dir():
                raise ValueError('Legacy source directory does not exist')
            # Reuse the original stable IDs; never guess a device ID from an IP address.
            devices = {device['device_id']: device for device in config.devices}
            if len(devices) != len(config.devices):
                raise ValueError('Duplicate configured device IDs')
            boundary = import_boundary(config.IMPORT_START_DATE)
            ledger = Ledger(path, readonly=not args.apply) if args.apply or path.exists() else None
            reports = []
            try:
                for dump in sorted(Path(args.directory).glob('*_last_fetch_dump.json')):
                    device_id = dump.name[:-len('_last_fetch_dump.json')]
                    if device_id not in devices:
                        reports.append(dict(source=str(dump), failed=1, error_category='unknown_device'))
                        continue
                    reports.append(import_dump(ledger, dump, devices[device_id], boundary, args.apply,
                        getattr(config, 'device_punch_values_IN', (0, 4)), getattr(config, 'device_punch_values_OUT', (1, 5))))
            finally:
                if ledger:
                    ledger.close()
            print(json.dumps(reports, indent=2))
            return int(any(report['failed'] for report in reports))
        return 0
    except Exception as exc:
        print('Operation failed: ' + type(exc).__name__ + '; inspect paths, permissions, or schema. Source evidence retained.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
