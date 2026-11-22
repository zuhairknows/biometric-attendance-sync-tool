"""Keep Windows PowerShell 5.1 fixtures out of an inherited PowerShell 7 module path."""
from contextlib import contextmanager
import os


@contextmanager
def native_modules():
    original = os.environ.get('PSModulePath')
    # A bundled pwsh host can export its own modules into Python children. WinPS
    # then imports the wrong Utility manifest and loses its Get-FileHash function.
    # Change only this serial test process's child-module search path, never the
    # machine/user environment or the production application's environment.
    os.environ['PSModulePath'] = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                                            'System32', 'WindowsPowerShell', 'v1.0', 'Modules')
    try:
        yield
    finally:
        if original is None:
            os.environ.pop('PSModulePath', None)
        else:
            os.environ['PSModulePath'] = original
