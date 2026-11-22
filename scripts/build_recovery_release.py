"""Build a source deployment ZIP from a clean reviewed checkout, never private state."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], check=True, capture_output=True).stdout


def build():
    if git('status', '--porcelain').strip():
        raise ValueError('Commit reviewed changes before building the release')
    revision = git('rev-parse', 'HEAD').decode().strip()
    if git('rev-parse', 'recovery').decode().strip() != revision:
        raise ValueError('The reviewed recovery branch must identify this checkout')
    files = [Path(name.decode()) for name in git('ls-files', '-z').split(b'\0') if name]
    # A clean Git index is the allowlist. Refuse private state even if accidentally
    # tracked; deployment must preserve the target's independently managed state.
    for path in files:
        name = path.name.lower()
        if ((name.startswith('local_config') and name != 'local_config.py.template')
                or path.suffix in ('.sqlite', '.sqlite3', '.db', '.backup') or path.is_absolute() or '..' in path.parts
                or not (ROOT / path).resolve().is_relative_to(ROOT) or (ROOT / path).is_symlink()
                or getattr((ROOT / path).lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Private or unsafe release input')
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    destination = output / ('biometric-recovery-' + revision[:12] + '.zip')
    bundle = destination.with_suffix('.bundle')
    if destination.exists() or bundle.exists():
        raise ValueError('Release artifact already exists')
    payloads = {p.as_posix(): (ROOT / p).read_bytes() for p in files}
    manifest = dict(schema='biometric-source-release-v1', commit=revision,
        python='3.11 x64', recovery_enabled=False,
        sha256={name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()})
    # Exclusive creation preserves any previously reviewed package at this SHA.
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in payloads.items():
            archive.writestr(name, data)
        archive.writestr('release-info.json', json.dumps(manifest, indent=2, sort_keys=True))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(digest + '  ' + destination.name + '\n')
    # The local recovery commits need not be pushed to install the reviewed ref.
    # A bundle supports a real fresh clone, including archived test objects.
    git('bundle', 'create', str(bundle), 'recovery')
    bundle_digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    bundle.with_suffix('.bundle.sha256').write_text(bundle_digest + '  ' + bundle.name + '\n')
    print(json.dumps(dict(file=str(destination), sha256=digest, bundle=str(bundle),
                         bundle_sha256=bundle_digest, commit=revision, files=len(files))))


if __name__ == '__main__':
    try:
        build()
    except Exception:
        print('Release build failed; require a clean checkout and a new destination.', file=sys.stderr)
        raise SystemExit(1) from None
