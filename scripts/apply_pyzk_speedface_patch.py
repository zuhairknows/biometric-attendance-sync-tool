from __future__ import annotations

import argparse
import importlib.metadata
import sys
from pathlib import Path

EXPECTED_PYZK_VERSION = "0.9"

OLD_BLOCK = """        else:
            while len(attendance_data) >= 40:
                uid, user_id, status, timestamp, punch, space = unpack('<H24sB4sB8s', attendance_data.ljust(40, b'\\x00')[:40])
                if self.verbose: print (codecs.encode(attendance_data[:40], 'hex'))
                user_id = (user_id.split(b'\\x00')[0]).decode(errors='ignore')
                timestamp = self.__decode_time(timestamp)

                attendance = Attendance(user_id, timestamp, status, punch, uid)
                attendances.append(attendance)
                attendance_data = attendance_data[40:]
"""

NEW_BLOCK = """        else:
            while len(attendance_data) >= 40:
                uid, user_id, status, timestamp, punch, space = unpack('<H24sB4sB8s', attendance_data.ljust(40, b'\\x00')[:40])
                if self.verbose: print (codecs.encode(attendance_data[:40], 'hex'))
                user_id = (user_id.split(b'\\x00')[0]).decode(errors='ignore')
                timestamp = self.__decode_time(timestamp)

                attendance = Attendance(user_id, timestamp, status, punch, uid)
                attendances.append(attendance)

                # SpeedFace-V5L uses 49-byte attendance records.
                if record_size == 49:
                    attendance_data = attendance_data[49:]
                else:
                    attendance_data = attendance_data[40:]
"""

PATCH_MARKER = "# SpeedFace-V5L uses 49-byte attendance records."


def get_pyzk_version():
    try:
        return importlib.metadata.version("pyzk")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("pyzk is not installed in this Python environment.") from exc


def locate_base_py():
    try:
        import zk.base
    except ImportError as exc:
        raise RuntimeError("Cannot import zk.base.") from exc
    path = Path(zk.base.__file__).resolve()
    if not path.is_file():
        raise RuntimeError(f"Could not locate pyzk base.py at: {path}")
    return path


def is_patched(source):
    return PATCH_MARKER in source and "attendance_data = attendance_data[49:]" in source


def validate_environment():
    version = get_pyzk_version()
    if version != EXPECTED_PYZK_VERSION:
        raise RuntimeError(
            f"Unsupported pyzk version: {version}. "
            f"This patch is verified only for pyzk {EXPECTED_PYZK_VERSION}."
        )
    base_path = locate_base_py()
    return version, base_path, base_path.read_text(encoding="utf-8")


def check_only():
    version, base_path, source = validate_environment()
    print(f"pyzk version : {version}")
    print(f"base.py      : {base_path}")
    if is_patched(source):
        print("Status       : PATCHED")
        return 0
    if OLD_BLOCK in source:
        print("Status       : UNPATCHED")
        return 2
    print("Status       : UNKNOWN SOURCE")
    return 3


def apply_patch():
    version, base_path, source = validate_environment()
    print(f"pyzk version : {version}")
    print(f"base.py      : {base_path}")

    if is_patched(source):
        print("SpeedFace patch already applied. No changes required.")
        return 0

    count = source.count(OLD_BLOCK)
    if count != 1:
        raise RuntimeError(
            f"Expected exactly one matching attendance block, found {count}. "
            "No file was modified."
        )

    backup_path = base_path.with_name(base_path.name + ".backup")
    if not backup_path.exists():
        backup_path.write_text(source, encoding="utf-8")
        print(f"Backup       : {backup_path}")
    else:
        print(f"Backup       : existing backup preserved at {backup_path}")

    base_path.write_text(source.replace(OLD_BLOCK, NEW_BLOCK, 1), encoding="utf-8")

    verification = base_path.read_text(encoding="utf-8")
    if not is_patched(verification):
        raise RuntimeError("Patch verification failed.")

    print("Status       : PATCH APPLIED")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        return check_only() if args.check else apply_patch()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
