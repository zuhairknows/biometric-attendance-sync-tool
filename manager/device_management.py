"""Presentation helpers for biometric device setup screens."""

import datetime

from config.schema import validate_device_id


CONNECTED = "Connected"
UNAVAILABLE = "Unavailable"
NOT_TESTED = "Not tested"
DISABLED = "Disabled"


def display_connectivity_status(enabled, test_state=None):
    """Return the customer-facing connectivity state for one device."""
    if not enabled:
        return DISABLED
    state = str(test_state or "").strip()
    if state == CONNECTED:
        return CONNECTED
    if state in (UNAVAILABLE, "Failed", "Connection failed"):
        return UNAVAILABLE
    return NOT_TESTED


def format_device_timestamp(value):
    if not value:
        return "No data"
    text = str(value).strip()
    if not text:
        return "No data"
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.datetime.fromisoformat(candidate)
            return parsed.strftime("%d %b %Y, %H:%M")
        except ValueError:
            pass
    return text


def validate_device_form_values(name, device_id, host, port, existing_device_ids=None):
    errors = []
    if not str(name or "").strip():
        errors.append("Device name is required.")

    clean_device_id = str(device_id or "").strip()
    if not clean_device_id:
        errors.append("Device ID is required.")
    else:
        try:
            validate_device_id(clean_device_id)
        except Exception as exc:
            errors.append(str(exc))

    if not str(host or "").strip():
        errors.append("Host/IP is required.")

    try:
        port_number = int(port)
        if port_number < 1 or port_number > 65535:
            errors.append("Port must be between 1 and 65535.")
    except (TypeError, ValueError):
        errors.append("Port must be a number between 1 and 65535.")

    existing = {str(value or "").strip() for value in (existing_device_ids or [])}
    if clean_device_id and clean_device_id in existing:
        errors.append("Device ID must be unique.")
    return errors


def friendly_device_failure_message(result):
    message = str(getattr(result, "message", "") or "").strip()
    if not message:
        return "Device is unavailable. Check the host/IP, port, password, and network connection."
    return "Device is unavailable. " + message


def summarize_connectivity_counts(statuses):
    counts = {
        CONNECTED: 0,
        UNAVAILABLE: 0,
        DISABLED: 0,
        NOT_TESTED: 0,
    }
    for status in statuses:
        counts[display_connectivity_status(True, status) if status != DISABLED else DISABLED] += 1
    return counts


def bulk_test_summary(statuses):
    counts = summarize_connectivity_counts(statuses)
    return (
        "Connected: "
        + str(counts[CONNECTED])
        + " | Unavailable: "
        + str(counts[UNAVAILABLE])
        + " | Disabled: "
        + str(counts[DISABLED])
    )
