"""Read-only investigation from a review PC; production requires explicit acknowledgement."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from historical_attendance.client import ReadOnlyFrappeClient, VerificationError
from historical_attendance.bounded import run_bounded
from historical_attendance.engine import investigate
from historical_attendance.reporting import export, validate_output
from historical_attendance.repository import SQLiteRepository
from scripts.attendance_reconciliation import local_time, verified_json
from scripts.capture_history_safety import check_directory_acl
from scripts.reconcile_attendance import SafeParser


def execute_investigation(*, source, start, end, devices, contract, site_url, max_requests,
                          request_timeout, compatibility_only, directory, contract_digest,
                          deadline_seconds, input_paths=(), request=None, acl_check=check_directory_acl):
    if request is None:
        import requests
        request = requests.request
    client = ReadOnlyFrappeClient(site_url, os.environ.get('INVESTIGATION_API_KEY', ''),
        os.environ.get('INVESTIGATION_API_SECRET', ''), request,
        max_requests=max_requests, timeout=request_timeout)
    rows, summary = investigate(SQLiteRepository(source), client, start, end, devices, contract,
                                compatibility_only=compatibility_only)
    summary['contract_file_sha256'] = contract_digest
    summary['supervisor_completion_required'] = True
    summary['execution_limits'] = dict(max_get_requests=max_requests,
        deadline_seconds=deadline_seconds, request_timeout_seconds=request_timeout)
    export(directory, rows, summary, ledger_path=source, input_paths=input_paths, acl_check=acl_check)
    # The supervisor needs only safe summary data, not another copy of private rows.
    return [], summary


def main(argv=None, *, request=None, acl_check=check_directory_acl, runner=run_bounded):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--ledger-copy', required=True, help='Absolute path to an offline development copy')
    parser.add_argument('--start', required=True, type=local_time)
    parser.add_argument('--end', required=True, type=local_time)
    parser.add_argument('--device', required=True, action='append', help='Repeat for each device ID')
    parser.add_argument('--site-url', required=True, help='HTTPS origin; credentials come from environment')
    parser.add_argument('--contract', required=True)
    parser.add_argument('--contract-sha256', required=True, help='Independently reviewed contract hash')
    parser.add_argument('--output', required=True, help='Existing private dedicated report directory')
    parser.add_argument('--max-get-requests', type=int, default=32, help='Hard total GET budget across both passes (default: 32)')
    parser.add_argument('--deadline-seconds', type=int, default=120, help='Hard investigation wall deadline (default: 120)')
    parser.add_argument('--request-timeout-seconds', type=int, default=10, help='Connect/read timeout (default: 10)')
    parser.add_argument('--production-read-only', action='store_true', help='Compatibility reads only; never reconcile production')
    parser.add_argument('--acknowledge-production', help='Repeat the exact production HTTPS origin as acknowledgement')
    failure_category = 'investigation_failed'
    try:
        args = parser.parse_args(argv)
        failure_category = 'investigation_contract_unverified'
        contract, digest, contract_path = verified_json(args.contract, args.contract_sha256)
        origin = args.site_url.rstrip('/')
        is_production = (urlsplit(origin).hostname == 'erp.myfpf.com'
                         or contract.get('destination_environment') == 'production')
        failure_category = 'investigation_limits_invalid'
        if not (1 <= args.max_get_requests <= 20000 and 1 <= args.deadline_seconds <= 600
                and 1 <= args.request_timeout_seconds <= 120):
            raise ValueError
        if is_production or args.production_read_only:
            failure_category = 'investigation_production_ack_required'
            if (not args.production_read_only or args.acknowledge_production != origin
                    or contract.get('destination_environment') != 'production'):
                raise ValueError
            failure_category = 'investigation_endpoint_safety_unverified'
            if contract.get('verification', {}).get('read_only_api_verified') is not True:
                raise ValueError
        elif args.acknowledge_production:
            failure_category = 'investigation_production_ack_required'
            raise ValueError
        failure_category = 'investigation_source_unavailable'
        source = Path(args.ledger_copy).resolve(strict=True)
        if not Path(args.ledger_copy).is_absolute():
            raise ValueError
        failure_category = 'investigation_output_unverified'
        directory = validate_output(args.output, ledger_path=source, input_paths=[contract_path], acl_check=acl_check)
        failure_category = 'investigation_execution_failed'
        rows, summary = runner(execute_investigation, dict(source=source, start=args.start, end=args.end,
            devices=args.device, contract=contract, site_url=args.site_url, max_requests=args.max_get_requests,
            request_timeout=args.request_timeout_seconds, compatibility_only=args.production_read_only,
            directory=directory, contract_digest=digest, deadline_seconds=args.deadline_seconds,
            request=request, acl_check=acl_check, input_paths=[contract_path]),
            seconds=args.deadline_seconds, directory=directory)
        receipt = dict(status=summary['status'], records=summary['totals']['records'])
        if args.production_read_only:
            receipt['compatibility_status'] = summary['compatibility_status']
        print(json.dumps(receipt))
        return 2 if summary['blockers'] else 0
    except KeyboardInterrupt:
        print(json.dumps({'status': 'interrupted', 'error_category': 'investigation_incomplete'}))
        return 130
    except VerificationError as exc:
        print(json.dumps({'status': 'failed', 'error_category': str(exc)}))
        return 1
    except Exception:
        # No exception text: HTTP/parser/file errors may include private input.
        print(json.dumps({'status': 'failed', 'error_category': failure_category}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
