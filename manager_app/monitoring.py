"""Safe monitor diagnostics; never alter the ledger's connection or schema checks."""
import inspect
from pathlib import Path
import sqlite3
import _sqlite3
import threading

from historical_attendance.diagnostics import Trace, DiagnosticError, STAGES, safe_code, MONITOR_SOURCE_BINDING_FIELDS
from sync_ledger import Ledger

# The successful standalone probe enforces this exact target. Do not export it or
# a reversible path hash; compare locally and publish only tri-state flags.
PROBE_TARGET = Path('C:/ProgramData/ERPNextBiometricPushService/sync.sqlite3')


# Associate trusted Ledger statements with fixed labels. Read source only on error;
# do not trace SQL, monkeypatch sqlite3, or add a second diagnostic DB connection.
_OPEN_STATEMENTS = (
    (Ledger.__init__, {
        "self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=10) if readonly else sqlite3.connect(str(self.path), timeout=10)": 'source_connect',
        "self.db.execute('PRAGMA busy_timeout=10000')": 'source_busy_timeout',
        "self.db.execute('PRAGMA foreign_keys=ON')": 'source_foreign_keys',
        'self.db.close()': 'source_close_on_error',
    }),
    (Ledger._initialize, {
        '''tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}''': 'source_tables',
        "versions = self.db.execute('SELECT version FROM schema_version').fetchall()": 'source_schema_version',
    }),
    (Ledger._validate_schema, {
        "reference = sqlite3.connect(':memory:')": 'reference_connect',
        'reference.execute(sql)': 'reference_schema_build',
        '''expected = reference.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()''': 'reference_schema_read',
        "actual = self.db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', (kind, name)).fetchone()": 'source_schema_compare',
        "rows = self.db.execute('SELECT id,cooldown_until,prefer_retry,prefer_recent FROM destination_state').fetchall()": 'source_destination_state',
        'reference.close()': 'reference_close',
    }),
)


def open_failure_operation(error):
    """Inspect trusted frames locally; emit only a fixed operation, never a trace."""
    operation = 'unknown'
    frame = error.__traceback__
    while frame:
        for function, statements in _OPEN_STATEMENTS:
            if frame.tb_frame.f_code is function.__code__:
                try:
                    lines, first = inspect.getsourcelines(function)
                    statement = lines[frame.tb_lineno - first].strip()
                    operation = statements.get(statement, operation)
                except (OSError, TypeError, IndexError):
                    pass  # Missing source must not replace or conceal the SQLite failure.
        frame = frame.tb_next
    return operation


def source_failure_binding(error, argument, profile_path):
    """Compare the failed constructor's actual path, without another SQLite open."""
    result = dict.fromkeys(MONITOR_SOURCE_BINDING_FIELDS)
    result.update(builtin_sqlite_connect=sqlite3.connect is _sqlite3.connect,
        background_thread=threading.current_thread() is not threading.main_thread())
    frame = error.__traceback__
    while frame:
        # Only read the known constructor's self.path/readonly, never arbitrary locals.
        if frame.tb_frame.f_code is _OPEN_STATEMENTS[0][0].__code__:
            try:
                path = frame.tb_frame.f_locals['self'].path
                result['readonly_argument'] = frame.tb_frame.f_locals['readonly'] is True
                if not isinstance(path, Path) or not path.is_absolute():
                    break
                actual_uri = path.as_uri() + '?mode=ro'
                # URI comparisons are exact, including spelling/escaping. Resolved
                # comparisons separately identify harmless path spelling differences.
                if isinstance(profile_path, str) and Path(profile_path).is_absolute():
                    profile = Path(profile_path)
                    result['argument_equals_profile'] = (actual_uri == profile.as_uri() + '?mode=ro'
                        and Path(argument).as_uri() + '?mode=ro' == actual_uri)
                    result['resolved_equals_profile'] = path.resolve().as_uri() == profile.resolve().as_uri()
                target = PROBE_TARGET.resolve()
                result['resolved_equals_probe_target'] = path.resolve().as_uri() == target.as_uri()
                result['uri_equals_probe'] = actual_uri == target.as_uri() + '?mode=ro'
            except Exception:
                pass  # Missing metadata is unknown, never a replacement for CANTOPEN.
            break
        frame = frame.tb_next
    return result


def failure_code(error, stage):
    # Numeric SQLite codes distinguish lock/IO failures without logging SQL or paths.
    if isinstance(error, sqlite3.Error):
        primary = getattr(error, 'sqlite_errorcode', 0) & 255
        codes = {sqlite3.SQLITE_BUSY: 'sqlite_busy', sqlite3.SQLITE_LOCKED: 'sqlite_locked',
            sqlite3.SQLITE_CANTOPEN: 'sqlite_cantopen', sqlite3.SQLITE_READONLY: 'sqlite_readonly',
            sqlite3.SQLITE_IOERR: 'sqlite_ioerr', sqlite3.SQLITE_CORRUPT: 'sqlite_corrupt',
            sqlite3.SQLITE_NOTADB: 'sqlite_notadb'}
        return codes.get(primary, 'sqlite_error')
    if stage == 'monitor_display' and isinstance(error, (ValueError, OverflowError, OSError, TypeError)):
        return 'diagnostic_value_invalid'
    return safe_code(error)


class MonitoringTrace:
    def __init__(self, controller, values, ledger_path):
        self.trace, self.warning = None, None
        self.stage, self.reference = 'monitor_path', 'not-recorded'
        self.ledger_path = ledger_path
        self.profile_ledger = values.get('monitor_ledger') if values is not None else None
        # Direct callers may omit logging; the Flask route supplies its private profile.
        if values is None:
            return
        try:
            directory = values.get('diagnostics_directory') or values.get('output', '')
            controller._paths(directory)
            output, source = Path(directory).resolve(), Path(ledger_path)
            if source.is_absolute():
                parent = source.resolve().parent
                # Check separation even if the database does not exist yet at boot.
                if output == parent or parent in output.parents:
                    raise ValueError('output_must_be_separate_from_inputs')
            self.trace = Trace(values, controller.acl_check, kind='monitor')
            self.reference = self.trace.reference
        except Exception as error:
            self.unavailable(error)

    def unavailable(self, error):
        code = error.code if isinstance(error, DiagnosticError) else safe_code(error)
        if isinstance(error, DiagnosticError):
            self.reference = error.reference
        self.warning = ('Private monitoring log unavailable (' + code
            + '). Check diagnostics_directory private ACLs, write access and separation from service/input directories. No console or temporary log fallback was used.')
        self.trace = None

    def emit(self, event, code, **details):
        if self.trace:
            try:
                self.trace.emit(event, code, **details)
            except Exception as error:
                self.unavailable(error)

    def enter(self, stage):
        self.stage = stage
        if self.trace:
            self.trace.stage = stage
        self.emit('check', 'ok')

    def failure(self, error):
        code = failure_code(error, self.stage)
        details, message = {}, ''
        number = getattr(error, 'sqlite_errorcode', None)
        if (self.stage == 'monitor_open_schema' and isinstance(error, sqlite3.Error)
                and type(number) is int and 0 <= number <= 2147483647):
            operation = open_failure_operation(error)
            details = dict(monitor_operation=operation, sqlite_code=number)
            message = f' Operation: {operation}; SQLite code: {number} (primary {number & 255}).'
            if operation == 'source_connect':
                binding = source_failure_binding(error, self.ledger_path, self.profile_ledger)
                details['monitor_source'] = binding
                # Values remain in private diagnostics; the browser gets a fixed hint.
                message += ' Source/profile and worker-context comparisons are in the private diagnostic record.'
        self.emit('failed', code, **details)
        return (f'Ledger unavailable: {self.stage} / {code}. Diagnostic {self.reference}. '
            + STAGES[self.stage] + message + ' No state was changed.')
