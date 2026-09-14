# Commercial Configuration

M3.1 adds a versioned JSON configuration layer while keeping existing `local_config.py` deployments working.
M3.2 adds first-run setup so a fresh installation can create this file without editing JSON by hand.
M3.3 stores commercial credentials as protected machine-local secrets and keeps only secret references in JSON.

## Precedence

The runtime loads configuration in this order:

1. `C:\ProgramData\BiometricAttendanceSync\config.json`
2. Legacy `local_config.py`
3. Safe application defaults

JSON configuration is the commercial customer-facing format. `local_config.py` remains supported for existing production installations and source/development usage.

## First-Run Detection

The product reports one of these states:

- `UNCONFIGURED`: no commercial `config.json` and no valid legacy `local_config.py`
- `CONFIGURED`: valid commercial `config.json`
- `LEGACY_CONFIGURED`: valid legacy `local_config.py` and no commercial `config.json`
- `INVALID`: a configuration file exists but cannot be loaded or validated

Safe application defaults never count as a configured installation.

## Fresh Installation Flow

On a fresh commercial installation, the Manager opens in `Not Configured` state and launches the first-run setup wizard. Synchronization stays disabled until setup completes and a valid `config.json` is written.

The setup wizard pages are:

1. Welcome
2. ERPNext Connection
3. Attendance Devices
4. Synchronization
5. Review
6. Validate & Finish

Finish validates the complete configuration, writes protected secret files, writes a temporary JSON file, validates that file through the commercial loader, and then atomically replaces `config.json`. If the save fails, the previous config and protected secret files are restored.

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
    "api_key_ref": "erpnext/api_key",
    "api_secret_ref": "erpnext/api_secret",
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
      "password_ref": "devices/DEVICE_01/password",
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

Customer configuration must provide the real ERPNext URL, API credentials, device list, and import start date. Commercial JSON may contain protected secret references or legacy plaintext credentials from an older M3.2 save. Existing plaintext JSON continues to load for compatibility, but saving through the Manager converts credentials to protected refs.

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

`LOGS_DIRECTORY` is optional for legacy installations. When it is absent, the normalized loader uses the existing ProgramData/runtime logs location instead of rejecting an otherwise valid legacy config.

Valid legacy installations continue operating and are not forced through first-run setup. M3.3 does not auto-migrate or rewrite legacy `local_config.py`; legacy plaintext credentials remain supported only for that compatibility path.

## Unconfigured Service Behavior

If the Windows service is installed before setup is complete, it stays idle and logs:

```text
Product is not configured. Complete first-run setup.
```

It does not connect to ERPNext, connect to devices, or create attendance synchronization work while unconfigured.

If commercial configuration exists but protected secrets are missing, corrupt, or cannot be decrypted, the status is `INVALID`. JSON configuration still takes precedence in this case; the runtime does not fall back to legacy `local_config.py`.

## Secrets

Commercial credentials are stored below:

```text
C:\ProgramData\BiometricAttendanceSync\secrets\
```

The secret store uses Windows DPAPI with machine scope so the interactive Manager can write secrets and the LocalSystem Windows service can read them on the same machine. This protects credentials at rest from casual file inspection, but any sufficiently privileged account on the same machine can still access or decrypt them. File ACL hardening should preserve Manager write access and LocalSystem read access.

The runtime still exposes normalized values as `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET`, and device `password` fields after the loader resolves refs. The sync engine does not call DPAPI directly.
