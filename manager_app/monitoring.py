"""Safe monitor diagnostics using the existing private, four-field JSONL writer."""
from pathlib import Path
import sqlite3

from historical_attendance.diagnostics import Trace, DiagnosticError, STAGES, safe_code


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

    def emit(self, event, code):
        if self.trace:
            try:
                self.trace.emit(event, code)
            except Exception as error:
                self.unavailable(error)

    def enter(self, stage):
        self.stage = stage
        if self.trace:
            self.trace.stage = stage
        self.emit('check', 'ok')

    def failure(self, error):
        code = failure_code(error, self.stage)
        self.emit('failed', code)
        return (f'Ledger unavailable: {self.stage} / {code}. Diagnostic {self.reference}. '
            + STAGES[self.stage] + ' No state was changed.')
