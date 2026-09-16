"""Device import/export helpers for Manager setup.

This module is intentionally UI-free. The wizard supplies file paths and shows
preview results, while this code only parses, validates, and shapes device rows.
"""

import csv
import io
from dataclasses import dataclass, field

from config.schema import validate_device_id, validate_port
from manager.setup.model import DeviceSetup


TEMPLATE_HEADERS = ["Device Name", "IP Address", "Port", "Device ID", "Enabled"]
VALID = "Valid"
INVALID = "Invalid"
CONFLICT = "Conflict"

HEADER_ALIASES = {
    "device name": "name",
    "name": "name",
    "ip address": "host",
    "ip": "host",
    "host": "host",
    "hostname": "host",
    "ip hostname": "host",
    "port": "port",
    "device id": "device_id",
    "deviceid": "device_id",
    "device": "device_id",
    "enabled": "enabled",
    "active": "enabled",
}

SENSITIVE_HEADER_TOKENS = ("api", "secret", "password", "token", "credential", "dpapi", "key")


@dataclass
class DeviceImportRow:
    row_number: int
    name: str = ""
    host: str = ""
    port: object = ""
    device_id: str = ""
    enabled: object = ""
    status: str = INVALID
    message: str = ""

    @property
    def is_valid(self):
        return self.status == VALID

    def to_device_setup(self):
        return DeviceSetup(
            name=self.name,
            device_id=self.device_id,
            ip=self.host,
            port=int(self.port),
            enabled=bool(self.enabled),
            password=0,
            clear_from_device_on_fetch=False,
        )


@dataclass
class DeviceImportPreview:
    rows: list = field(default_factory=list)

    @property
    def valid_rows(self):
        return [row for row in self.rows if row.status == VALID]

    @property
    def invalid_count(self):
        return len([row for row in self.rows if row.status == INVALID])

    @property
    def conflict_count(self):
        return len([row for row in self.rows if row.status == CONFLICT])

    @property
    def can_apply(self):
        return bool(self.valid_rows) and self.invalid_count == 0 and self.conflict_count == 0


class DeviceImportError(ValueError):
    pass


def parse_csv_text(text, existing_device_ids=None):
    existing_device_ids = _normalize_existing_ids(existing_device_ids)
    try:
        reader = csv.DictReader(io.StringIO(text), strict=True)
        raw_headers = reader.fieldnames or []
        header_map = normalize_headers(raw_headers)
        _validate_required_headers(header_map)
        rows = [
            _validate_import_row(row_number=index + 2, raw_row=raw_row, header_map=header_map)
            for index, raw_row in enumerate(reader)
        ]
    except csv.Error as exc:
        raise DeviceImportError("The import file could not be read as CSV. " + str(exc)) from exc

    _mark_duplicate_and_conflicting_ids(rows, existing_device_ids)
    return DeviceImportPreview(rows)


def read_csv_file(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return parse_csv_text(handle.read())


def parse_csv_file(path, existing_device_ids=None):
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return parse_csv_text(handle.read(), existing_device_ids=existing_device_ids)


def normalize_headers(headers):
    header_map = {}
    for header in headers:
        normalized = _normalize_header(header)
        if _is_sensitive_header(normalized):
            raise DeviceImportError("The import file contains an unsupported sensitive column: " + str(header))
        field = HEADER_ALIASES.get(normalized)
        if field and field not in header_map:
            header_map[field] = header
    return header_map


def apply_preview_to_devices(existing_devices, preview):
    if preview.invalid_count or preview.conflict_count:
        raise DeviceImportError("Fix import errors before applying devices.")
    return list(existing_devices) + [row.to_device_setup() for row in preview.valid_rows]


def export_devices_csv(devices):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=TEMPLATE_HEADERS, lineterminator="\n")
    writer.writeheader()
    for device in devices:
        writer.writerow(export_device_row(device))
    return output.getvalue()


def export_device_row(device):
    return {
        "Device Name": str(getattr(device, "name", "") or ""),
        "IP Address": str(getattr(device, "ip", "") or ""),
        "Port": str(getattr(device, "port", 4370)),
        "Device ID": str(getattr(device, "device_id", "") or ""),
        "Enabled": "Yes" if bool(getattr(device, "enabled", True)) else "No",
    }


def template_csv(include_example=False):
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(TEMPLATE_HEADERS)
    if include_example:
        writer.writerow(["Main Entrance", "192.0.2.20", "4370", "DEVICE_01", "Yes"])
    return output.getvalue()


def parse_enabled(value):
    text = str(value or "").strip().lower()
    if text in ("yes", "y", "true", "1"):
        return True
    if text in ("no", "n", "false", "0"):
        return False
    raise ValueError("Enabled must be Yes, No, True, False, 1, or 0.")


def _validate_required_headers(header_map):
    missing = []
    for field, label in [("name", "Device Name"), ("host", "IP Address"), ("port", "Port"), ("device_id", "Device ID"), ("enabled", "Enabled")]:
        if field not in header_map:
            missing.append(label)
    if missing:
        raise DeviceImportError("The import file is missing the '" + missing[0] + "' column.")


def _validate_import_row(row_number, raw_row, header_map):
    row = DeviceImportRow(row_number=row_number)
    errors = []
    row.name = _cell(raw_row, header_map["name"])
    row.host = _cell(raw_row, header_map["host"])
    port_text = _cell(raw_row, header_map["port"])
    row.device_id = _cell(raw_row, header_map["device_id"])
    enabled_text = _cell(raw_row, header_map["enabled"])

    if not row.name:
        errors.append("Device Name is required.")
    if not row.host:
        errors.append("Host/IP is required.")
    if not row.device_id:
        errors.append("Device ID is required.")
    else:
        try:
            validate_device_id(row.device_id)
        except ValueError as exc:
            errors.append(str(exc))
    try:
        row.port = validate_port(port_text)
    except ValueError:
        errors.append("Port must be between 1 and 65535.")
    try:
        row.enabled = parse_enabled(enabled_text)
    except ValueError as exc:
        errors.append(str(exc))

    if errors:
        row.status = INVALID
        row.message = " ".join(errors)
    else:
        row.status = VALID
        row.message = "Valid"
    return row


def _mark_duplicate_and_conflicting_ids(rows, existing_device_ids):
    seen = {}
    for row in rows:
        if not row.device_id:
            continue
        if row.device_id in seen and row.status == VALID:
            row.status = INVALID
            row.message = "Duplicate Device ID within import file."
        elif row.device_id in seen and row.status != VALID:
            row.message = (row.message + " Duplicate Device ID within import file.").strip()
        seen[row.device_id] = True
    for row in rows:
        if row.status == VALID and row.device_id in existing_device_ids:
            row.status = CONFLICT
            row.message = "Device ID '" + row.device_id + "' already exists."


def _cell(raw_row, header):
    return str(raw_row.get(header, "") or "").strip()


def _normalize_existing_ids(existing_device_ids):
    return {str(device_id or "").strip() for device_id in (existing_device_ids or []) if str(device_id or "").strip()}


def _normalize_header(header):
    return " ".join(str(header or "").lstrip("\ufeff").strip().lower().replace("_", " ").replace("-", " ").replace("/", " ").split())


def _is_sensitive_header(normalized_header):
    return any(token in normalized_header.split() or token in normalized_header for token in SENSITIVE_HEADER_TOKENS)
