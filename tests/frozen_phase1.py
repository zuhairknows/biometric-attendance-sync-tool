"""Materialize the archived Phase 1 sources for their original certification tests.

Use only local Git objects at the immutable reviewed revision. The branch under
development has its own source pins and behavioral tests; never replace frozen
certification hashes with the new implementation's hashes.
"""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

from scripts.phase1_staging_engine_https import HASHES, REVISION
from scripts.phase1_staging_scm_host import WRAPPER_HASHES


def materialize(repository):
    temporary = tempfile.TemporaryDirectory(prefix='phase1-frozen-sources-')
    root = Path(temporary.name)
    try:
        shutil.copytree(repository / 'scripts', root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        for name, digest in dict(HASHES, **WRAPPER_HASHES).items():
            data = subprocess.run(['git', '-C', str(repository), 'show', REVISION + ':' + name],
                                  check=True, capture_output=True, timeout=15).stdout
            # The archived Windows service wrappers were certified with CRLF;
            # durable modules were certified with LF. Only accept the exact pin.
            if hashlib.sha256(data).hexdigest() != digest:
                data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('archived_phase1_source_mismatch')
            (root / name).write_bytes(data)
        return temporary, root
    except BaseException:
        temporary.cleanup()
        raise
