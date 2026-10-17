"""Investigate a copied ledger and ERPNext via GET only; never run on production."""
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from historical_attendance.client import ReadOnlyFrappeClient
from historical_attendance.engine import investigate
from historical_attendance.reporting import export
from historical_attendance.repository import SQLiteRepository
from scripts.attendance_reconciliation import local_time, verified_json
from scripts.capture_history_safety import preflight_output, check_directory_acl
from scripts.reconcile_attendance import SafeParser


def main(argv=None, *, request=None, acl_check=check_directory_acl):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--ledger-copy', required=True, help='Absolute path to an offline development copy')
    parser.add_argument('--start', required=True, type=local_time)
    parser.add_argument('--end', required=True, type=local_time)
    parser.add_argument('--device', required=True, action='append', help='Repeat for each device ID')
    parser.add_argument('--site-url', required=True, help='HTTPS origin; credentials come from environment')
    parser.add_argument('--contract', required=True)
    parser.add_argument('--contract-sha256', required=True, help='Independently reviewed contract hash')
    parser.add_argument('--output', required=True, help='Existing private dedicated report directory')
    try:
        args = parser.parse_args(argv)
        contract, digest, contract_path = verified_json(args.contract, args.contract_sha256)
        source = Path(args.ledger_copy).resolve(strict=True)
        if not Path(args.ledger_copy).is_absolute():
            raise ValueError
        directory = preflight_output(args.output, live=False, acl_check=acl_check)
        for path in (source, contract_path):
            if directory == path.parent or path.parent in directory.parents:
                raise ValueError
        if request is None:
            import requests
            request = requests.request
        client = ReadOnlyFrappeClient(args.site_url, os.environ.get('INVESTIGATION_API_KEY', ''),
                                     os.environ.get('INVESTIGATION_API_SECRET', ''), request)
        rows, summary = investigate(SQLiteRepository(source), client, args.start, args.end,
                                    args.device, contract)
        summary['contract_file_sha256'] = digest
        export(directory, rows, summary)
        print(json.dumps({'status': summary['status'], 'records': len(rows)}))
        return 2 if summary['blockers'] else 0
    except KeyboardInterrupt:
        print(json.dumps({'status': 'interrupted', 'error_category': 'investigation_incomplete'}))
        return 130
    except Exception:
        # No exception text: HTTP/parser/file errors may include private input.
        print(json.dumps({'status': 'failed', 'error_category': 'investigation_failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
