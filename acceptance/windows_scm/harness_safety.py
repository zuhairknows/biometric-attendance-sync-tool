"""Harness-only safety helpers. No application imports or machine mutations at import."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

RUN = "A4F20179C01"
CANDIDATE = "4f20179f5fb10f84a6f86a6ff720f9c2876efc20"
ROOT = Path("C:/FPFPhase2AclAcceptance-" + RUN)
FINAL = Path("C:/FPFPhase2AclEvidence-" + RUN)
MUTEX = r"Global\ERPNextBiometricPushService-DurableWorker"
LEASE = r"Global\FPFPhase2AclAcceptance_A4F20179C01-Worker"


def need(value, code):
    if not value:
        raise RuntimeError(code)


class MutexAPI:
    """Never wait on another worker's mutex. Native query failure is a hard stop."""
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.nt = ctypes.WinDLL("ntdll")
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
        self.api.CreateMutexW.restype = w.HANDLE
        self.api.OpenMutexW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
        self.api.OpenMutexW.restype = w.HANDLE
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.api.ReleaseMutex.argtypes = [w.HANDLE]
        self.api.ReleaseMutex.restype = w.BOOL
        self.nt.NtQueryMutant.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                        w.ULONG, ctypes.POINTER(w.ULONG)]
        self.nt.NtQueryMutant.restype = w.LONG

    def create_new(self, name):
        ctypes.set_last_error(0)
        handle = self.api.CreateMutexW(None, True, name)
        error = ctypes.get_last_error()
        if not handle or error:
            if handle:
                self.api.CloseHandle(handle)
            raise RuntimeError("mutex_exists_or_creation_denied")
        return handle

    def owner(self, name):
        # MUTANT_QUERY_STATE; query does not acquire or create a mutex.
        handle = self.api.OpenMutexW(1, False, name)
        need(handle, "mutex_owner_unavailable")
        class Owner(ctypes.Structure):
            _fields_ = [("pid", ctypes.c_void_p), ("tid", ctypes.c_void_p)]
        value, length = Owner(), w.ULONG()
        try:
            status = self.nt.NtQueryMutant(handle, 1, ctypes.byref(value),
                                           ctypes.sizeof(value), ctypes.byref(length))
            need(status == 0 and length.value == ctypes.sizeof(value), "mutex_owner_query_failed")
            return value.pid or 0, value.tid or 0
        finally:
            self.api.CloseHandle(handle)

    def release(self, handle):
        try:
            need(self.api.ReleaseMutex(handle), "mutex_release_failed")
        finally:
            self.api.CloseHandle(handle)


def assert_owner(pid, api=None):
    api = api or MutexAPI()
    need(pid > 0, "expected_worker_PID_required")
    observed = {}
    for name in (LEASE, MUTEX):
        owner, tid = api.owner(name)
        need(owner == pid and tid > 0, "expected_worker_lost_mutex")
        observed[name] = dict(pid=owner, tid=tid)
    need(len({v["tid"] for v in observed.values()}) == 1, "worker_mutex_threads_differ")
    return observed


@contextmanager
def exclusive_worker(api=None):
    """Create-only claim closes the preflight/start race; original worker recurses on this thread."""
    api = api or MutexAPI()
    handles = []
    try:
        for name in (LEASE, MUTEX):
            handles.append(api.create_new(name))
        assert_owner(os.getpid(), api)
        yield lambda: assert_owner(os.getpid(), api)
    finally:
        for handle in reversed(handles):
            api.release(handle)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(directory):
    """Validate before recursion, including junctions; reject case/ADS/path ambiguities."""
    result = {}
    def walk(parent):
        for item in parent.iterdir():
            need(not item.is_symlink() and not getattr(item.lstat(), "st_file_attributes", 0) & 0x400,
                 "evidence_reparse_refused")
            relative = item.relative_to(directory).as_posix()
            need(":" not in relative and "\\" not in relative, "evidence_path_escape")
            if item.is_dir():
                walk(item)
            else:
                need(item.is_file(), "evidence_special_file")
                result[relative] = sha(item)
    need(directory.is_dir() and not directory.is_symlink()
         and not getattr(directory.lstat(), "st_file_attributes", 0) & 0x400, "evidence_directory_required")
    walk(directory)
    return result


def identity():
    return dict(schema="fpf-scm-evidence-v2", run=RUN, candidate=CANDIDATE, root=str(ROOT))


def write_json(path, value):
    # Exclusive creation: a failed attempt is never silently replaced.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def attempt_path(attempt):
    need(str(uuid.UUID(attempt)) == attempt, "canonical_attempt_UUID_required")
    return ROOT / "evidence-staging" / attempt


def validate_attempt(path):
    need(path.parent == ROOT / "evidence-staging" and str(uuid.UUID(path.name)) == path.name,
         "unrecognized_attempt")
    observed = files(path)
    marker = path / "attempt.json"
    expected = json.dumps(identity(), sort_keys=True).encode("utf-8")
    if not marker.exists():
        # A crash between mkdir and the first exclusive marker write leaves only an empty UUID directory.
        need(not observed and not list(path.iterdir()), "unidentified_attempt_payload")
    else:
        raw = marker.read_bytes()
        if raw != expected:
            # Recover only an interrupted initial write, never an arbitrary identity or payload.
            need(set(observed) == {"attempt.json"} and expected.startswith(raw)
                 and len(raw) < len(expected) and len(list(path.iterdir())) == 1,
                 "attempt_identity_mismatch")


def begin():
    need(not FINAL.exists(), "existing_evidence_refused")
    parent = ROOT / "evidence-staging"
    parent.mkdir(exist_ok=True)
    # Parent/root ACL and reparse checks are performed by the privileged driver.
    attempt = parent / str(uuid.uuid4())
    attempt.mkdir()
    write_json(attempt / "attempt.json", identity())
    (attempt / "payload").mkdir()
    return attempt


def validate(directory):
    observed = files(directory)
    expected = json.loads((directory / "sha256.json").read_text(encoding="utf-8-sig"))
    need(expected["identity"] == identity(), "evidence_identity_mismatch")
    observed.pop("sha256.json")
    need(observed == expected["files"], "evidence_hash_mismatch")
    need(set(expected["required"]).issubset(observed), "evidence_incomplete")
    need({"receipt.json", "summary.json", "ACLs.json", "partials.json"}.issubset(observed),
         "evidence_incomplete")
    receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8-sig"))
    need(receipt["candidate"] == CANDIDATE and receipt["root"] == str(ROOT)
         and receipt["service"] == "FPFPhase2AclAcceptance_" + RUN, "evidence_receipt_mismatch")
    if receipt["prepared"]:
        need({"ledger.sqlite3", "synthetic.sqlite3.activation.json"}.issubset(observed),
             "prepared_backup_required")
    return expected


def seal_publish(attempt, required, inject=lambda step: None):
    """Interrupted attempts remain intact. A retry uses a new UUID and archives all old attempts."""
    validate_attempt(attempt)
    payload = attempt / "payload"
    partials = {}
    for old in sorted(attempt.parent.iterdir()):
        if old == attempt:
            continue
        validate_attempt(old)
        prior = files(old)
        partials[old.name] = prior
        shutil.copytree(old, payload / "partials" / old.name)
    write_json(payload / "partials.json", partials)
    inject("after_partials")
    entries = files(payload)
    need(set(required).issubset(entries), "evidence_incomplete")
    write_json(payload / "sha256.json",
               dict(identity=identity(), attempt=attempt.name, required=sorted(set(required)), files=entries))
    inject("after_manifest")
    validate(payload)
    inject("before_publish")
    need(not FINAL.exists(), "existing_evidence_refused")
    # Windows rename fails if the destination exists. Same C: volume, no copy fallback.
    os.rename(payload, FINAL)
    inject("after_publish")
    validate(FINAL)
    return FINAL


def validate_cleanup():
    """Every partial attempt must be retained byte-for-byte before deleting the disposable root."""
    manifest = validate(FINAL)
    archived = json.loads((FINAL / "partials.json").read_text())
    staging = ROOT / "evidence-staging"
    if staging.exists():
        for attempt in staging.iterdir():
            validate_attempt(attempt)
            current = files(attempt)
            # Published attempt retains only its identity marker.
            if attempt.name == manifest["attempt"] and current == {"attempt.json": sha(attempt / "attempt.json")}:
                continue
            need(archived.get(attempt.name) == current, "partial_evidence_not_preserved")
