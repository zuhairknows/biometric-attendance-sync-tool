"""Enforce a wall-clock investigation deadline, including stalled HTTP reads."""
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time

from .client import VerificationError


def _worker(task, options, result_path):
    try:
        rows, summary = task(**options)
        result = dict(ok=True, rows=rows, summary=summary)
    except BaseException:
        # Child tracebacks can contain private paths, URLs or credentials.
        result = dict(ok=False)
    with open(result_path, 'w', encoding='utf-8') as stream:
        json.dump(result, stream)


def run_bounded(task, options, *, seconds, directory, cancelled=lambda: False):
    """Run in an isolated process; no success receipt is returned after deadline.

    directory must already pass reporting.validate_output. IPC is a private
    temporary file, avoiding pipe/queue deadlocks on attendance-sized responses.
    Any report artifacts left after supervisor failure are incomplete evidence.
    """
    if cancelled():
        raise VerificationError('investigation_cancelled')
    began = time.monotonic()
    fd, name = tempfile.mkstemp(prefix='.investigation-worker-', suffix='.part', dir=directory)
    os.close(fd)
    process = multiprocessing.get_context('spawn').Process(target=_worker, args=(task, options, name), daemon=True)
    try:
        if cancelled():
            raise VerificationError('investigation_cancelled')
        process.start()
        while process.is_alive():
            if cancelled():
                raise VerificationError('investigation_cancelled')
            remaining = seconds - (time.monotonic() - began)
            if remaining <= 0:
                break
            process.join(min(0.1, remaining))
        if cancelled():
            raise VerificationError('investigation_cancelled')
        if process.is_alive() or time.monotonic() - began >= seconds:
            raise VerificationError('investigation_deadline_exceeded')
        if process.exitcode != 0:
            raise VerificationError('investigation_worker_failed')
        result = json.loads(Path(name).read_text(encoding='utf-8'))
        if time.monotonic() - began >= seconds:
            raise VerificationError('investigation_deadline_exceeded')
        if result.get('ok') is not True:
            raise VerificationError('investigation_worker_failed')
        return result['rows'], result['summary']
    finally:
        # Kill the HTTP worker before returning a failure; it must not keep reading.
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(1)
                if process.is_alive():
                    process.kill()
                    process.join(1)
            process.close()
        Path(name).unlink(missing_ok=True)
