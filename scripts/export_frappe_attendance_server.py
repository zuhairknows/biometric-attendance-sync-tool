"""Run with Bench's Python from an approved admin shell; never bench execute/API."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.attendance_reconciliation import ReconciliationError, require
from scripts.export_frappe_attendance import main as export_main
from scripts.frappe_server_transport import ServerTransport, connect_internal


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ReconciliationError('invalid_server_arguments_use_help')


def main(argv=None, *, connector=connect_internal, password_reader=getpass.getpass, host=None):
    parser = SafeParser(description=__doc__ + ' Remaining options are the existing export CLI options.', allow_abbrev=False)
    parser.add_argument('--bench-root', required=True)
    parser.add_argument('--site', required=True, help='Approved internal Bench site directory name')
    parser.add_argument('--internal-profile', required=True, help='Approved non-secret Unix socket/account profile')
    previous_umask = None
    try:
        args, remaining = parser.parse_known_args(argv)
        require(not any(v == '--db-profile' or v.startswith('--db-profile=') for v in remaining), 'use_internal_profile_only')
        # Never run within a Frappe/Bench lifecycle that may commit or reuse a writable connection.
        require('frappe' not in sys.modules, 'run_standalone_in_bench_python_not_frappe_console')
        transport = ServerTransport(args.bench_root, args.site, host=host)
        previous_umask = os.umask(0o077)
        return export_main(remaining + ['--db-profile', args.internal_profile], connector=connector,
                           password_reader=password_reader, server_transport=transport)
    except ReconciliationError as exc:
        print(json.dumps(dict(status='failed', error_category=str(exc), certification_issued=False)))
        return 1
    except Exception:
        print(json.dumps(dict(status='failed', error_category='server_preflight_failed', certification_issued=False)))
        return 1
    finally:
        if previous_umask is not None:
            os.umask(previous_umask)


if __name__ == '__main__':
    raise SystemExit(main())
