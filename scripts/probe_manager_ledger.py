"""Standalone metadata-only probe; run with the manager's Python -I -S -B.

No configuration imports, source writes, full scans, service/device/API operations
or raw exception output. This is not a dashboard health certificate or repair.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import quote

LEDGER_SHA256 = '5e6713142f432b808c7bba50a5f55798733c5c981657e381f09576fde7bc7a2d'
TARGET = Path('C:/ProgramData/ERPNextBiometricPushService/sync.sqlite3')
SOURCE_LABEL = '<verified-sync-ledger>'


def failure(step, error):
    record = dict(step=step, status='FAIL', category='unexpected')
    if isinstance(error, sqlite3.Error):
        record['category'] = 'sqlite'
        code = getattr(error, 'sqlite_errorcode', None)
        if type(code) is int:
            record.update(sqlite_code=code, sqlite_primary=code & 255)
        name = getattr(error, 'sqlite_errorname', '')
        if isinstance(name, str) and re.fullmatch(r'SQLITE_[A-Z0-9_]+', name):
            record['sqlite_name'] = name
    elif isinstance(error, (ValueError, RuntimeError, OSError, TypeError, KeyError)):
        record['category'] = 'validation_or_access'
    # One safe source location, not a traceback, path, SQL statement or exception text.
    frame = error.__traceback__
    while frame:
        code = frame.tb_frame.f_code
        if code.co_filename == SOURCE_LABEL and code.co_name in ('__init__', '_initialize', '_validate_schema'):
            record.update(ledger_method=code.co_name, ledger_line=frame.tb_lineno)
        frame = frame.tb_next
    return record


def probe(manager, profile, *, expected_ledger=TARGET, emit=print):
    def report(**record):
        emit(json.dumps(record, sort_keys=True))

    stage, failed = 'profile_and_source_binding', False
    try:
        manager, profile = Path(manager), Path(profile)
        if not manager.is_absolute() or not profile.is_absolute():
            raise ValueError()
        raw = profile.read_bytes()
        if len(raw) > 65536:
            raise ValueError()
        values = json.loads(raw.decode('utf-8'))
        value = values['monitor_ledger']
        if (not isinstance(value, str) or len(value) > 4096 or not Path(value).is_absolute()
                or value.startswith('\\\\')):
            raise ValueError()
        # Match the startup profile's Path.resolve(), not a separately typed DB path.
        path = Path(value).resolve()
        if path != Path(expected_ledger).resolve():
            raise ValueError()
        source = (manager / 'sync_ledger.py').read_bytes()
        if hashlib.sha256(source.replace(b'\r\n', b'\n')).hexdigest() != LEDGER_SHA256:
            raise ValueError()
        namespace = {'__name__': 'verified_ledger_probe'}
        # Execute only the exact reviewed stdlib-only module, from the hashed bytes.
        # Never import local_config.py or initialize the synchronization application.
        exec(compile(source, SOURCE_LABEL, 'exec'), namespace)
        report(step=stage, status='PASS', target_matches=True, source_verified=True,
            python='.'.join(map(str, sys.version_info[:3])), sqlite=sqlite3.sqlite_version)
        uris = [('manager_uri', path.as_uri() + '?mode=ro'),
                ('short_uri', 'file:' + quote(path.as_posix(), safe='/:') + '?mode=ro')]
        for label, uri in uris:
            stage = label + '_connect'
            try:
                with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db:
                    report(step=stage, status='PASS')
                    stage = label + '_schema_read'
                    db.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
                    report(step=stage, status='PASS')
            except Exception as error:
                failed = True
                emit(json.dumps(failure(stage, error), sort_keys=True))
        stage = 'exact_ledger_constructor'
        with namespace['Ledger'](path, readonly=True):
            report(step=stage, status='PASS')
        stage = 'profile_recheck'
        if profile.read_bytes() != raw:
            raise ValueError()
        report(step=stage, status='PASS')
        return 1 if failed else 0
    except Exception as error:
        emit(json.dumps(failure(stage, error), sort_keys=True))
        return 1


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print('{"step":"arguments","status":"FAIL"}')
        return 1
    return probe(*args)


if __name__ == '__main__':
    raise SystemExit(main())
