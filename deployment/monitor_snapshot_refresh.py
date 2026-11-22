"""Refresh the monitoring dashboard's copy of the sync ledger.

Run by the scheduled task from Install-MonitorSnapshotTask.ps1 (as SYSTEM, with the
service's Python and -I):

    python -I monitor_snapshot_refresh.py <service local_config.py> <snapshot folder>

The live ledger is opened read-only and copied with SQLite's online backup API, so
the service keeps running and is never changed. The copy is first made into a
staging file, checked, then swapped into <snapshot>\\sync.sqlite3 in one backup
step, so the dashboard sees either the previous or the new copy, never a partial
one. local_config.monitor.py is rewritten without the API key/secret or device
passwords. refresh-status.json records the outcome of every run.
"""
import ast
import datetime as dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = r'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3'
KEEP = {'devices', 'shift_type_device_mapping', 'DURABLE_DB_PATH', 'IMPORT_START_DATE', 'PULL_FREQUENCY',
        'SHIFT_SYNC_RETRY_GRACE_HOURS', 'SHIFT_SYNC_DEVICE_STALE_HOURS', 'SHIFT_SYNC_FUTURE_TOLERANCE_MINUTES',
        'SHIFT_SYNC_IGNORED_DEVICES'}


def monitor_config(config_path):
    """Literal values the dashboard reads; never executes the service configuration."""
    tree = ast.parse(Path(config_path).read_text(encoding='utf-8-sig'))
    values = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in KEEP):
            values[node.targets[0].id] = ast.literal_eval(node.value)
    for device in values.get('devices', []):
        device.pop('password', None)  # secrets never leave the service configuration
    lines = ['# Monitoring copy: secrets removed. Not usable by the sync service.']
    lines += ['%s = %r' % (key, values[key]) for key in sorted(values)]
    return values, '\n'.join(lines) + '\n'


def write_atomic(path, text):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text, encoding='utf-8')
    os.replace(temp, path)


def refresh(config_path, out):
    values, text = monitor_config(config_path)
    write_atomic(out / 'local_config.monitor.py', text)

    source_path = Path(values.get('DURABLE_DB_PATH', DEFAULT_DB))
    staging = out / '.staging.sqlite3'
    for leftover in (staging, staging.with_name(staging.name + '-journal')):
        if leftover.exists():
            leftover.unlink()

    source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True, timeout=30)
    try:
        stage = sqlite3.connect(str(staging))
        try:
            source.backup(stage, pages=4096, sleep=0.05)
        finally:
            stage.close()
    finally:
        source.close()

    stage = sqlite3.connect(str(staging))
    try:
        if stage.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'delete':
            stage.execute('PRAGMA journal_mode=DELETE')  # readers need no -wal/-shm access
        check = stage.execute('PRAGMA quick_check').fetchone()[0]
        if check != 'ok':
            raise RuntimeError('staging copy failed quick_check: %s' % check)
        punches = stage.execute('SELECT COUNT(*) FROM punches').fetchone()[0]
        target = sqlite3.connect(str(out / 'sync.sqlite3'), timeout=60)
        try:
            stage.backup(target)  # one step: readers see the old or the new copy
        finally:
            target.close()
    finally:
        stage.close()
    staging.unlink()
    return dict(ledger=str(source_path), punches=punches, quick_check=check)


def main(argv):
    if len(argv) != 3:
        print('usage: monitor_snapshot_refresh.py <local_config.py> <snapshot folder>', file=sys.stderr)
        return 2
    config_path, out = Path(argv[1]), Path(argv[2])
    began = time.time()
    status = dict(finished_at=None, ok=False)
    try:
        status.update(refresh(config_path, out), ok=True)
    except Exception as error:  # recorded for the dashboard user; the next run retries
        status['error'] = '%s: %s' % (type(error).__name__, error)
    status['finished_at'] = dt.datetime.now().isoformat(' ', 'seconds')
    status['seconds'] = round(time.time() - began, 1)
    write_atomic(out / 'refresh-status.json', json.dumps(status, indent=2) + '\n')
    print(json.dumps(status))
    return 0 if status['ok'] else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
