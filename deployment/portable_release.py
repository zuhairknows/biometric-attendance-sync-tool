"""Portable package and deployment checks. No service or ACL operations here.

Private configuration is parsed as literal data, never executed by this tool.
The PowerShell driver is the sole owner of privileged mutations.
"""
import argparse
import ast
from contextlib import closing
import datetime as dt
from email.parser import BytesParser
import hashlib
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sqlite3
import subprocess
import sys
import zipfile

SCHEMA = "fpf-portable-release-v1"
SERVICE = "ERPNextBiometricPushService"
MAX_BYTES = 256 * 1024 * 1024
PRIVATE = (".sqlite", ".sqlite3", ".db", ".backup", ".key", ".pem", ".pfx", ".pyc", "-journal", "-wal", "-shm", ".activation.json", ".env")
RESERVED = {"con", "prn", "aux", "nul", *("com" + str(i) for i in range(1, 10)),
            *("lpt" + str(i) for i in range(1, 10))}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    """ZIP paths must also be safe when interpreted by Windows, including ADS."""
    parts = PurePosixPath(name).parts
    require(name and not name.startswith("/") and "\\" not in name and ":" not in name
            and "\0" not in name and all(p not in ("", ".", "..") for p in name.split("/"))
            and all(p.rstrip(" .") == p and p.split(".")[0].lower() not in RESERVED
                    for p in parts), "unsafe_package_path")
    return name


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(source):
    result = {}
    for line in source.decode("utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9.+!-]+)", line.strip())
        require(match is not None, "requirements_must_be_frozen")
        name, version = match.groups()
        require(normalized(name) not in result, "duplicate_pin")
        result[normalized(name)] = version
    require(len(result) == 19, "expected_19_accepted_pins")
    return result


def wheel_payloads(directory, approved_digest, requirements):
    directory = Path(directory)
    require(not directory.is_symlink(), "wheelhouse_link")
    manifest = (directory / "SHA256SUMS.txt").read_bytes()
    require(digest(manifest) == approved_digest, "unapproved_wheel_manifest")
    expected, result, found = pins(requirements), {}, {}
    for row in manifest.decode("utf-8-sig").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.+-]+\.whl)", row)
        require(match is not None, "invalid_wheel_manifest")
        checksum, name = match.groups()
        require(name not in result, "duplicate_wheel")
        path = directory / name
        require(not path.is_symlink() and not getattr(path.stat(), "st_file_attributes", 0) & 0x400,
                "wheel_link")
        content = path.read_bytes()
        require(digest(content) == checksum, "wheel_hash_mismatch")
        with zipfile.ZipFile(io.BytesIO(content)) as wheel:
            metadata_names = [n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")]
            require(len(metadata_names) == 1, "wheel_metadata")
            metadata = BytesParser().parsebytes(wheel.read(metadata_names[0]))
        from pip._vendor.packaging.tags import sys_tags
        from pip._vendor.packaging.utils import parse_wheel_filename
        from pip._vendor.packaging.specifiers import SpecifierSet
        filename_package, filename_version, _, tags = parse_wheel_filename(name)
        require(bool(tags & set(sys_tags())), "wheel_architecture")
        require(not metadata.get("Requires-Python") or
                SpecifierSet(metadata["Requires-Python"]).contains("3.11.9"), "wheel_python_version")
        package, version = normalized(metadata["Name"]), metadata["Version"]
        require(normalized(filename_package) == package and str(filename_version) == version, "wheel_filename_metadata")
        require(package not in found and expected.get(package) == version, "wheel_pin_mismatch")
        found[package] = version
        result[name] = content
    require(found == expected and set(result) == {p.name for p in directory.glob("*.whl")},
            "missing_or_extra_wheel")
    return result, manifest


def git_bytes(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def build(root, wheels, approved_digest, version, destination):
    root, destination = Path(root).resolve(), Path(destination)
    require(re.fullmatch(r"[0-9][A-Za-z0-9_.-]{0,60}", version), "invalid_version")
    source = git_bytes(root, "rev-parse", "HEAD").decode().strip()
    dirty = bool(git_bytes(root, "status", "--porcelain").strip())
    tracked = git_bytes(root, "ls-files", "-z").decode().split("\0")
    # Only these new hardening files may augment tracked sources in a review candidate.
    additions = [str(p.relative_to(root)).replace("\\", "/")
                 for directory in (root / "deployment",)
                 for p in directory.rglob("*") if p.is_file() and p.suffix in (".py", ".ps1", ".md", ".template")]
    additions += [str(p.relative_to(root)).replace("\\", "/")
                  for p in (root / "docs").glob("portable-*.md")]
    names = sorted(set(filter(None, tracked + additions)))
    payload = {}
    for name in names:
        safe_name(name)
        path = root / name
        require(not path.is_symlink() and not getattr(path.stat(), "st_file_attributes", 0) & 0x400,
                "source_link")
        lower = name.lower()
        require(not any(part in (".git", ".acceptance", ".venv", "__pycache__")
                        for part in PurePosixPath(lower).parts)
                and not lower.endswith(PRIVATE)
                and path.name.lower() not in ("web-profile.json", "credentials.json", "employee-checkins.csv", "employee-mappings.json")
                and (not path.name.lower().startswith("local_config")
                     or path.name.endswith(".template")), "private_release_input")
        content = path.read_bytes() if dirty else git_bytes(root, "show", source + ":" + name)
        # The native-wrapper acceptance pins CRLF; other clean sources use exact
        # Git blob bytes, independent of the builder's autocrlf settings.
        if path.name in ("SMWinservice.py", "erpnext_sync_win.py"):
            content = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        payload["code/" + name] = content
    requirements = payload["code/docs/requirements-windows311-accepted.txt"]
    binaries, manifest = wheel_payloads(wheels, approved_digest, requirements)
    payload.update({"wheels/" + name: data for name, data in binaries.items()})
    payload["wheels/SHA256SUMS.txt"] = manifest
    accepted = pins(requirements)
    lock = [normalized(name.split("-")[0]) + "==" + accepted[normalized(name.split("-")[0])] +
            " --hash=sha256:" + digest(data) for name, data in sorted(binaries.items())]
    payload["wheels/requirements.lock"] = ("\n".join(lock) + "\n").encode()
    hashes = {name: digest(data) for name, data in sorted(payload.items())}
    tree_hash = digest(json.dumps(hashes, sort_keys=True).encode())
    info = dict(schema=SCHEMA, version=version, release_id=version + "-" + tree_hash[:12],
                source_commit=source, uncommitted_candidate=dirty, python="3.11.9-x64",
                recovery_enabled=False, wheel_manifest_sha256=approved_digest, sha256=hashes)
    payload["release-info.json"] = json.dumps(info, indent=2, sort_keys=True).encode() + b"\n"
    require(not destination.exists() and not destination.with_suffix(destination.suffix + ".sha256").exists(),
            "output_exists")
    # Fixed entry dates/order/mode and no platform compression variation.
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED) as bundle:
        for name, content in sorted(payload.items()):
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.create_system = 0
            entry.external_attr = 0
            bundle.writestr(entry, content)
    checksum = digest(destination.read_bytes())
    with destination.with_suffix(destination.suffix + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(checksum + "  " + destination.name + "\n")
    return dict(release_id=info["release_id"], sha256=checksum,
                uncommitted_candidate=dirty, files=len(payload))


def verify_tree(root):
    root = Path(root)
    info = json.loads((root / "release-info.json").read_text(encoding="utf-8"))
    require(info["schema"] == SCHEMA and info["recovery_enabled"] is False
            and info["python"] == "3.11.9-x64"
            and re.fullmatch(r"[0-9][A-Za-z0-9_.-]{0,80}", info["release_id"]), "release_manifest")
    hashes = info["sha256"]
    actual = set()
    for path in root.rglob("*"):
        require(not path.is_symlink() and not getattr(path.stat(), "st_file_attributes", 0) & 0x400,
                "release_link")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    require(actual == set(hashes) | {"release-info.json"}, "release_file_set")
    for name, checksum in hashes.items():
        safe_name(name)
        require(digest((root / name).read_bytes()) == checksum, "release_hash")
    wheel_payloads(root / "wheels", info["wheel_manifest_sha256"],
                   (root / "code/docs/requirements-windows311-accepted.txt").read_bytes())
    return info


def configuration(path):
    """A dynamic config needs separate migration review, not privileged execution."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        require(isinstance(node, ast.Assign), "config_requires_literal_review")
        value = ast.literal_eval(node.value)
        for target in node.targets:
            require(isinstance(target, ast.Name) and target.id not in values, "config_ambiguous")
            values[target.id] = value
    require(values.get("DURABLE_SYNC_ENABLED") is True
            and values.get("HISTORICAL_RECOVERY_ENABLED") is False
            and values.get("HISTORICAL_RECOVERY_COORDINATION") is False, "recovery_or_durable_gate")
    for key in ("DURABLE_DB_PATH", "LOGS_DIRECTORY"):
        require(isinstance(values.get(key), str) and Path(values[key]).is_absolute(), "absolute_state_paths_required")
    from urllib.parse import urlsplit
    site = urlsplit(values.get("ERPNEXT_URL", ""))
    require(site.scheme == "https" and site.hostname and site.path in ("", "/")
            and not site.username and not site.password and not site.query and not site.fragment
            and not site.hostname.endswith((".example", ".invalid")), "destination_invalid")
    for key in ("ERPNEXT_API_KEY", "ERPNEXT_API_SECRET"):
        value = values.get(key)
        require(isinstance(value, str) and value.strip() and
                not any(marker in value.upper() for marker in ("YOUR_", "REPLACE_", "<")), "credentials_not_configured")
    from datetime import datetime
    datetime.strptime(values["IMPORT_START_DATE"], "%Y%m%d")
    devices = values.get("devices")
    require(isinstance(devices, list) and devices, "devices_required")
    ids = set()
    for device in devices:
        identity = device.get("device_id", "")
        require(re.fullmatch(r"[A-Za-z0-9_-]+", identity) and identity not in ids,
                "device_id_invalid_or_duplicate")
        ids.add(identity)
        require(device.get("clear_from_device_on_fetch") is False, "device_deletion_forbidden")
        require(isinstance(device.get("ip"), str) and device["ip"].strip()
                and type(device.get("port", 4370)) is int and 1 <= device.get("port", 4370) <= 65535,
                "device_address_invalid")
    for mapping in values.get("shift_type_device_mapping", []):
        require(set(mapping["related_device_id"]) <= ids, "unknown_shift_device")
    # Policy settings fail here, before installation, instead of at service startup.
    for key in ("SHIFT_SYNC_RETRY_GRACE_HOURS", "SHIFT_SYNC_DEVICE_STALE_HOURS", "SHIFT_SYNC_FUTURE_TOLERANCE_MINUTES"):
        value = values.get(key, 0)
        require(type(value) in (int, float) and value >= 0, "shift_sync_setting_invalid")
    ignored = values.get("SHIFT_SYNC_IGNORED_DEVICES", [])
    require(isinstance(ignored, (list, tuple)) and all(isinstance(d, str) for d in ignored)
            and set(ignored) <= ids, "shift_sync_ignored_device_unknown")
    if values.get("DURABLE_RESEND_VERIFIED_MISSING", False) is not False:
        reference = values.get("DURABLE_SERVER_REVIEW_REFERENCE")
        require(values.get("DURABLE_RESEND_VERIFIED_MISSING") is True
                and values.get("DURABLE_CONFIRM_READS_VERIFIED") is True
                and isinstance(reference, str) and reference.strip(), "resend_requires_verified_reads")
    settle = values.get("DURABLE_RESEND_SETTLE_SECONDS", 600)
    require(type(settle) in (int, float) and settle >= 60, "resend_settle_invalid")
    return values


def config_summary(path):
    c = configuration(path)
    return dict(ledger=c["DURABLE_DB_PATH"], logs=c["LOGS_DIRECTORY"],
                monitor_sid=c.get("DURABLE_MONITOR_READONLY_SID"),
                devices=[d["device_id"] for d in c["devices"]])


def code_imports(code):
    # Only reviewed stdlib/ledger helpers; never import erpnext_sync/local_config.
    sys.path.insert(0, str(Path(code).resolve()))
    from sync_ledger import Ledger
    from durable_startup import validate_activation, canonical_storage
    return Ledger, validate_activation, canonical_storage


def readonly_state(code, config):
    c = configuration(config)
    Ledger, activation, _ = code_imports(code)
    module = __import__("types").ModuleType("local_config")
    module.__dict__.update(c)
    module.__file__ = str(Path(config).absolute())
    binding = activation(Path(c["DURABLE_DB_PATH"]), module)
    with Ledger(Path(c["DURABLE_DB_PATH"]), readonly=True) as ledger:
        ledger.verify_integrity()
        names = {r[0] for r in ledger.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        count = ledger.db.execute("SELECT COUNT(*) FROM sr_intents").fetchone()[0] if "sr_intents" in names else 0
        return dict(schema_version=1, recovery_intents=count, activation=binding,
                    diagnostics=ledger.diagnostics())


def backup(code, config, output):
    c = configuration(config)
    Ledger, _, _ = code_imports(code)
    source, output = Path(c["DURABLE_DB_PATH"]), Path(output)
    require(output.is_absolute() and output.parent.is_dir() and not output.exists()
            and not output.with_suffix(".activation.json").exists()
            and output.resolve() != source.resolve(), "backup_destination")
    # Caller proves quiescence. Still use SQLite backup, never raw-copy a live DB.
    with Ledger(source, readonly=True) as ledger:
        ledger.verify_integrity()
        with closing(sqlite3.connect(str(output))) as target:
            ledger.db.backup(target)
    with Ledger(output, readonly=True) as saved:
        saved.verify_integrity()
    marker = Path(str(source) + ".activation.json").read_bytes()
    with output.with_suffix(".activation.json").open("xb") as stream:
        stream.write(marker)
    return dict(database_sha256=digest(output.read_bytes()), activation_sha256=digest(marker))


def network(config):
    """Explicit operator request: TCP checks and one TLS GET; no device commands/POST."""
    from urllib.parse import urlsplit
    c = configuration(config)
    for device in c["devices"]:
        with socket.create_connection((device["ip"], device.get("port", 4370)), timeout=5):
            pass
    site = urlsplit(c["ERPNEXT_URL"])
    connection = http.client.HTTPSConnection(site.hostname, site.port or 443, timeout=10)
    try:
        connection.request("GET", "/api/method/ping", headers={
            "Authorization": "token " + c["ERPNEXT_API_KEY"] + ":" + c["ERPNEXT_API_SECRET"]})
        response = connection.getresponse()
        require(response.status == 200, "erpnext_readonly_probe_failed")
        response.read(65536)
    finally:
        connection.close()
    return dict(tcp_devices=len(c["devices"]), tls_get="PASS", permission_acceptance="PENDING")


def health(code, config, since):
    c = configuration(config)
    boundary = dt.datetime.fromisoformat(since)
    observed, healthy, delivered, critical = set(), False, False, False
    # Read bounded recent logs. Failure to retain enough evidence is NO-GO.
    for name in ("logs.log.1", "logs.log", "error.log.1", "error.log"):
        path = Path(c["LOGS_DIRECTORY"]) / name
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - 2 * 1024 * 1024)
            stream.seek(start)
            if start:
                stream.readline()  # Discard a partial first record.
            lines = stream.read(2 * 1024 * 1024).decode("utf-8", errors="replace").splitlines()
            for line in lines[-5000:]:
                fields = line.split("\t", 2)
                if len(fields) != 3:
                    continue
                try:
                    stamp = dt.datetime.strptime(fields[0], "%Y-%m-%d %H:%M:%S,%f")
                    if stamp < boundary:
                        continue
                    event = json.loads(fields[2])
                except (ValueError, TypeError):
                    continue
                kind = event.get("event")
                healthy |= kind == "durable_health"
                critical |= kind == "durable_critical"
                if kind == "durable_capture":
                    observed.add(event.get("device_id"))
                if kind == "durable_delivery":
                    delivered |= event.get("http") == 200 and event.get("state") in ("synced", "duplicate")
    require(not critical, "critical_startup_or_storage_failure")
    ready = healthy and observed >= {d["device_id"] for d in c["devices"]}
    # Do not repeatedly scan a live ledger while waiting for the next poll.
    state = readonly_state(code, config) if ready and delivered else {"diagnostics": {}}
    return dict(healthy=ready, delivery_observed=delivered,
                devices_observed=len(observed), diagnostics=state["diagnostics"])


def installed_runtime(root):
    """Check all pins and the whole patched pyzk source, not just its marker."""
    import importlib.metadata
    root = Path(root)
    expected = pins((root / "code/docs/requirements-windows311-accepted.txt").read_bytes())
    for name, version in expected.items():
        require(importlib.metadata.version(name) == version, "installed_pin_mismatch")
    patch_tree = ast.parse((root / "code/scripts/apply_pyzk_speedface_patch.py").read_text(encoding="utf-8-sig"))
    blocks = {n.targets[0].id: ast.literal_eval(n.value) for n in patch_tree.body
              if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
              and n.targets[0].id in ("OLD_BLOCK", "NEW_BLOCK")}
    with zipfile.ZipFile(next((root / "wheels").glob("pyzk-0.9-*.whl"))) as wheel:
        original = wheel.read("zk/base.py").decode("utf-8").replace("\r\n", "\n")
    require(original.count(blocks["OLD_BLOCK"]) == 1, "pyzk_source_mismatch")
    patched = original.replace(blocks["OLD_BLOCK"], blocks["NEW_BLOCK"], 1)
    installed = importlib.metadata.distribution("pyzk").locate_file("zk/base.py")
    require(Path(installed).read_text(encoding="utf-8") == patched, "pyzk_patch_bytes_mismatch")
    return dict(pins_verified=len(expected), speedface="EXACT_PATCH_PASS")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("build", "verify", "config", "state", "backup", "network", "health", "provision", "runtime"))
    p.add_argument("--root"); p.add_argument("--wheels"); p.add_argument("--wheel-manifest-sha256")
    p.add_argument("--version"); p.add_argument("--output")
    p.add_argument("--code"); p.add_argument("--config"); p.add_argument("--since")
    p.add_argument("--cutover"); p.add_argument("--reference")
    a = p.parse_args()
    try:
        if a.action == "build":
            result = build(a.root, a.wheels, a.wheel_manifest_sha256, a.version, a.output)
        elif a.action == "verify":
            result = verify_tree(a.root)
        elif a.action == "runtime":
            result = installed_runtime(a.root)
        elif a.action == "config":
            result = config_summary(a.config)
        elif a.action == "network":
            result = network(a.config)
        elif a.action == "state":
            result = readonly_state(a.code, a.config)
        elif a.action == "backup":
            result = backup(a.code, a.config, a.output)
        elif a.action == "health":
            result = health(a.code, a.config, a.since)
        else:
            c = configuration(a.config)
            code_imports(a.code)
            from durable_startup import provision
            result = provision(c["DURABLE_DB_PATH"], c["ERPNEXT_URL"], c.get("ERPNEXT_VERSION", 14),
                               a.cutover, a.reference)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        # No exception text/traceback: config syntax, URLs and HTTP failures can contain secrets.
        code = str(error) if isinstance(error, ValueError) and re.fullmatch("[a-z_]+", str(error)) else type(error).__name__
        print(json.dumps(dict(status="FAIL", code=code)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
