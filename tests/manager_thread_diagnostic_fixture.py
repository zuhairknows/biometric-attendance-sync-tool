"""Synthetic-only main/State-worker comparison. Never imported by the manager."""
from contextlib import closing
import json
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import uuid

from historical_attendance.reporting import validate_output
from manager_app.controller import ManagerError


class ThreadOpenDiagnostic:
    def __init__(self, state):
        self.state, self.used = state, False
        # Fail closed outside the existing synthetic demo sandbox.
        self.root = state.controller.sandbox_root
        self.source = Path(state.values.get('monitor_ledger', ''))
        if not self.root or not self.source.resolve().is_relative_to(self.root):
            raise ValueError('synthetic_sandbox_required')

    def binding(self):
        source = self.source
        if (not source.is_absolute() or not source.resolve().is_relative_to(self.root)
                or Path(self.state.values.get('monitor_ledger', '')) != source):
            raise ValueError('monitor_source_binding_unverified')
        return source.as_uri() + '?mode=ro'

    def open_result(self, operation, uri):
        result = dict(operation=operation, result='unavailable', sqlite_code=None, sqlite_primary=None)
        try:
            if self.state.closed or self.binding() != uri:
                return result
            # Exactly the source-connect arguments used by Ledger. No schema SQL,
            # file repair, service query, sidecar manipulation or fallback transport.
            with closing(sqlite3.connect(uri, uri=True, timeout=10)):
                pass
            result['result'] = 'passed'
        except sqlite3.Error as error:
            result['result'] = 'failed'
            number = getattr(error, 'sqlite_errorcode', None)
            if type(number) is int and 0 <= number <= 2147483647:
                result.update(sqlite_code=number, sqlite_primary=number & 255)
        except Exception:
            pass  # Never serialize exception text, paths, SQL or process context.
        return result

    def trigger(self, *_ignored):
        # SIGBREAK handlers execute on Python's actual main thread. A second
        # signal cannot re-enter an active diagnostic or produce repeated opens.
        if self.used or threading.current_thread() is not threading.main_thread():
            return
        self.used = True
        ready = threading.Event()
        can_open = False
        try:
            uri = self.binding()
            values, controller = self.state.values, self.state.controller
            directory = values.get('diagnostics_directory') or values.get('output', '')
            controller._paths(str(self.source), directory)
            directory = validate_output(directory, ledger_path=self.source, acl_check=controller.acl_check)
            report = directory / ('sqlite-thread-open-' + uuid.uuid4().hex + '.jsonl')

            def worker():
                ready.wait()
                if not can_open:
                    raise ManagerError('monitor_thread_diagnostic_unavailable')
                if report.is_symlink() or report.resolve().parent != directory:
                    raise ManagerError('monitor_thread_diagnostic_unavailable')
                # Open the existing private report before touching SQLite. The
                # main connection and its report stream have already closed.
                with report.open('r+', encoding='utf-8') as stream:
                    stream.seek(0, 2)
                    stream.write(json.dumps(self.open_result('worker_open', uri)) + '\n')
                    stream.flush()

            try:
                self.state.start('Read-only SQLite thread comparison', worker, lambda _: None, '/')
            except ManagerError:
                self.used = False  # Busy manager: skip without a file or DB open.
                print('SQLite thread comparison skipped: manager busy or closing.')
                return
            # State.start owns the normal job lock; its worker waits until the
            # main connection is closed. No refresh or other job can overlap.
            with report.open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(self.open_result('main_open', uri)) + '\n')
                stream.flush()
            can_open = True
        except Exception:
            print('SQLite thread comparison unavailable. No private exception details were printed.')
        finally:
            ready.set()


def install(state):
    """Opt-in Windows console signal, restored by runtime shutdown. No polling."""
    if sys.platform != 'win32' or threading.current_thread() is not threading.main_thread():
        raise ValueError('windows_main_thread_required')
    diagnostic = ThreadOpenDiagnostic(state)
    previous = signal.signal(signal.SIGBREAK, diagnostic.trigger)
    print('SQLite thread comparison armed: press Ctrl+Break once after a failed refresh.')
    return lambda: signal.signal(signal.SIGBREAK, previous)
