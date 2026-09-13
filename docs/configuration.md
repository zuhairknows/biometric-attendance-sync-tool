# Commercial Configuration

M3.1 adds a versioned JSON configuration layer while keeping existing `local_config.py` deployments working.

## Precedence

The runtime loads configuration in this order:

1. `C:\ProgramData\BiometricAttendanceSync\config.json`
2. Legacy `local_config.py`
3. Safe application defaults

JSON configuration is the commercial customer-facing format. `local_config.py` remains supported for existing production installations and source/development usage.

## ProgramData Layout

Mutable customer-specific data belongs under:

```text
C:\ProgramData\BiometricAttendanceSync\
    config.json
    state\
        state.json
    logs\
    retry\
    secrets\
```

Program Files or the repository folder should not be the primary location for customer configuration, logs, state, retry data, or credentials in commercial deployments.

## JSON Schema Version

`config.json` must include:

```json
{
  "schema_version": 1
}
```

Unsupported schema versions fail with a clear configuration error. Future migrations can branch from this value.

## Example Shape

Use placeholders only in shared examples. Do not commit real URLs, credentials, device addresses, employee IDs, or customer-specific names.

```json
{
  "schema_version": 1,
  "erpnext": {
    "url": "https://erp.example.com",
    "api_key": "YOUR_API_KEY",
    "api_secret": "YOUR_API_SECRET",
    "verify_ssl": true,
    "request_timeout_seconds": 30
  },
  "devices": [
    {
      "device_id": "DEVICE_01",
      "name": "Main Office",
      "ip": "192.0.2.10",
      "port": 4370,
      "enabled": true,
      "clear_from_device_on_fetch": false
    }
  ],
  "sync": {
    "pull_frequency_minutes": 60,
    "import_start_date": "2026-09-13"
  },
  "logging": {
    "level": "INFO",
    "retention_days": 30
  }
}
```

## Defaults Versus Customer Configuration

Application defaults are only safe fallbacks that let modules import and diagnostics report clear errors. They are not a production setup.

Customer configuration must provide the real ERPNext URL, API credentials, device list, and import start date.

## Legacy Compatibility

Existing `local_config.py` settings continue to map directly to the sync engine:

- `ERPNEXT_URL`
- `ERPNEXT_API_KEY`
- `ERPNEXT_API_SECRET`
- `ERPNEXT_VERSION`
- `PULL_FREQUENCY`
- `LOGS_DIRECTORY`
- `IMPORT_START_DATE`
- `ERPNEXT_REQUEST_TIMEOUT` or `REQUEST_TIMEOUT`
- `devices`
- `shift_type_device_mapping`
- `allowed_exceptions`
- `device_punch_values_IN`
- `device_punch_values_OUT`

The commercial loader presents JSON configuration as the same legacy-shaped runtime object so the current sync behavior stays stable.

## Secrets

M3.1 introduces the path and runtime abstraction for secrets:

```text
C:\ProgramData\BiometricAttendanceSync\secrets\
```

Final Windows credential protection is intentionally deferred to M3.3. Until then, do not commit real secrets, and do not print secret values in validation errors or UI messages.
