"""Prepare a private offline attendance recovery preview. Execution is unavailable."""
import datetime as dt
import json
from pathlib import Path
import sys
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.attendance_reconciliation import ReconciliationError, require
from scripts.attendance_recovery_preview import build_preview, write_preview
from scripts.capture_history_safety import CaptureError, preflight_output, check_directory_acl
from scripts.reconcile_attendance import SafeParser, publish


def main(argv=None, *, acl_check=check_directory_acl, now=None):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    for name in ('capture-summary', 'exports-manifest', 'review-manifest'):
        parser.add_argument('--' + name, required=True)
        parser.add_argument('--' + name + '-sha256', required=True,
                            help='Independently trusted hash; do not derive trust from this same input')
    parser.add_argument('--output', required=True, help='Existing private local directory outside repository/evidence')
    try:
        args = parser.parse_args(argv)
        rows, summary, inputs = build_preview(args.capture_summary, args.capture_summary_sha256,
            args.exports_manifest, args.exports_manifest_sha256, args.review_manifest,
            args.review_manifest_sha256, now=now)
        directory = preflight_output(args.output, live=False, acl_check=acl_check)
        for path in inputs:
            require(directory != path.parent and path.parent not in directory.parents,
                    'output_must_be_separate_from_inputs')
        run = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex
        summary['reports'] = [publish(directory, 'recovery_preview_' + run + '.csv',
                                      lambda stream: write_preview(stream, rows))]
        report = publish(directory, 'recovery_preview_' + run + '.json',
                         lambda stream: json.dump(summary, stream, indent=2, allow_nan=False))
        # Never echo paths, employee identifiers, input values, or exception messages.
        print(json.dumps(dict(status='blocked' if summary['blockers'] else 'preview_complete',
            execution_supported=False, recovery_authorized=False, counts=summary['counts'],
            blockers=summary['blockers'], review_fingerprint=summary['review_fingerprint'], report=report)))
        return 2 if summary['blockers'] else 0
    except (ReconciliationError, CaptureError):
        print(json.dumps(dict(status='failed', error_category='input_validation_or_private_output_check_failed')))
        return 1
    except KeyboardInterrupt:
        print(json.dumps(dict(status='failed', error_category='interrupted_partial_reports_possible')))
        return 130
    except Exception:
        print(json.dumps(dict(status='failed', error_category='invalid_inputs_or_report_write_failed')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
