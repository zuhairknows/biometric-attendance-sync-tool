"""Compare verified local evidence and separately prepared Frappe exports; never deliver."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.attendance_reconciliation import (ReconciliationError, load_captures, load_exports,
    local_time, reconcile, require, write_details)
from scripts.capture_history_safety import preflight_output, finalize_no_replace, check_directory_acl


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ReconciliationError('invalid_arguments_use_help')


def publish(directory, name, write):
    fd, temporary = tempfile.mkstemp(prefix='.reconciliation-', suffix='.part', dir=directory)
    # Failed writes retain a protected partial file; no successful manifest is produced.
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
        write(stream)
        stream.flush()
        os.fsync(stream.fileno())
    final = directory / name
    finalize_no_replace(Path(temporary), final)
    digest = hashlib.sha256(final.read_bytes()).hexdigest()
    return {'file': name, 'sha256': digest}


def main(argv=None, *, acl_check=check_directory_acl):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--capture-summary', required=True)
    parser.add_argument('--capture-summary-sha256', required=True, help='Independently verified original summary hash')
    parser.add_argument('--exports-manifest', required=True)
    parser.add_argument('--exports-manifest-sha256', required=True, help='Trusted hash of the reviewed export manifest')
    parser.add_argument('--output', required=True, help='Existing protected dedicated directory outside input/production folders')
    parser.add_argument('--start', type=local_time, default=dt.datetime(2026, 10, 11))
    parser.add_argument('--end', type=local_time, default=dt.datetime(2026, 10, 15, 23, 59, 59, 999999))
    parser.add_argument('--site-timezone', default='Asia/Riyadh')
    try:
        args = parser.parse_args(argv)
        require(args.start <= args.end, 'invalid_date_range')
        captures, sources, paths = load_captures(args.capture_summary, args.capture_summary_sha256)
        contract, mappings, checkins, export_sources, export_paths = load_exports(
            args.exports_manifest, args.exports_manifest_sha256, args.start, args.end, args.site_timezone)
        exported_at = dt.datetime.fromisoformat(contract['exported_at'])
        require(all(dt.datetime.fromisoformat(c['capture_finished_at']) <= exported_at for c, _ in captures),
                'exports_predate_capture')
        directory = preflight_output(args.output, live=False, acl_check=acl_check)
        # Evidence is read-only even when an operator accidentally chooses its directory.
        for path in paths + export_paths:
            require(directory != path.parent and path.parent not in directory.parents, 'output_must_be_separate_from_inputs')
        rows, summary = reconcile(captures, contract, mappings, checkins, args.start, args.end, args.site_timezone)
        summary['inputs'] = sources + export_sources
        run = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex
        details = publish(directory, 'reconciliation_details_' + run + '.csv', lambda stream: write_details(stream, rows))
        summary['reports'] = [details]
        summary['created_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        result = publish(directory, 'reconciliation_summary_' + run + '.json',
                         lambda stream: json.dump(summary, stream, indent=2, allow_nan=False))
        # Summary contains counts/hashes only, never mapping IDs, paths, names, or CSV contents.
        print(json.dumps(dict(status='complete', **summary, summary_report=result), indent=2))
        return 0
    except ReconciliationError as exc:
        print(json.dumps({'status': 'failed', 'error_category': str(exc)}))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({'status': 'failed', 'error_category': 'operator_interrupted_partial_reports_possible'}))
        return 130
    except Exception:
        # Parser, filesystem and CSV exceptions may echo private values or paths.
        print(json.dumps({'status': 'failed', 'error_category': 'invalid_evidence_export_or_report_write_failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
