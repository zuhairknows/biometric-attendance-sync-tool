"""Explicit Phase 2B batches. Only synthetic execution is available; no live transport."""
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from historical_attendance.recovery import prepare, approve, execute, results, export_results
from historical_attendance.recovery_store import RecoveryStore
from historical_attendance.recovery_simulation import SimulationTarget
from scripts.reconcile_attendance import SafeParser
from scripts.capture_history_safety import preflight_output


def main(argv=None):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--workspace', required=True, help='Existing private canonical recovery workspace outside checkout/inputs')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('prepare')
    for flag in ('ledger-copy', 'summary', 'summary-sha256', 'contract', 'contract-sha256', 'receipt-review'):
        create.add_argument('--' + flag, required=True)
    for name in ('approve', 'execute-simulation', 'show', 'export'):
        command = commands.add_parser(name)
        command.add_argument('--batch', required=True)
        if name in ('approve', 'execute-simulation'):
            command.add_argument('--fingerprint', required=True)
        if name == 'approve':
            command.add_argument('--record-id', type=int, action='append', required=True)
            command.add_argument('--approval-reference', required=True)
        if name == 'execute-simulation':
            command.add_argument('--simulation', required=True, help='Private synthetic destination JSON; never an API URL')
            command.add_argument('--normal-worker-path', required=True, help='Synthetic Phase 1 lock binding; Windows uses global mutex')
        if name == 'export':
            command.add_argument('--output', required=True)
    store = None
    try:
        args = parser.parse_args(argv)
        workspace = preflight_output(args.workspace)
        store = RecoveryStore(workspace)
        if args.command == 'prepare':
            batch, fingerprint = prepare(store, args.ledger_copy, args.summary, args.summary_sha256,
                args.contract, args.contract_sha256, receipt_review=args.receipt_review)
            print(json.dumps(dict(batch=batch, fingerprint=fingerprint, counts=results(store, batch)['counts'])))
        elif args.command == 'approve':
            approve(store, args.batch, args.fingerprint, args.record_id, args.approval_reference)
            print(json.dumps(dict(batch=args.batch, counts=results(store, args.batch)['counts'])))
        elif args.command == 'execute-simulation':
            target = SimulationTarget(args.simulation)
            execute(store, args.batch, args.fingerprint, target, Path(args.normal_worker_path))
            counts = results(store, args.batch)['counts']
            print(json.dumps(dict(batch=args.batch, counts=counts, production_execution_supported=False)))
            return 2 if any(counts.get(s) for s in ('Approved', 'Needs Review', 'Failed')) else 0
        elif args.command == 'show':
            print(json.dumps(dict(batch=args.batch, counts=results(store, args.batch)['counts'])))
        else:
            export_results(store, args.batch, args.output)
            print(json.dumps(dict(batch=args.batch, status='exported')))
        return 0
    except KeyboardInterrupt:
        print(json.dumps(dict(status='interrupted', error_category='recovery_incomplete_recheck_audit')))
        return 130
    except Exception:
        # Input errors and remote exceptions must never print identifiers or credentials.
        print(json.dumps(dict(status='blocked', error_category='recovery_validation_or_execution_failed')))
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
