"""Data model for the first-run setup wizard."""

import datetime
from dataclasses import dataclass, field


@dataclass
class ERPNextSetup:
    url: str = ""
    api_key: str = ""
    api_secret: str = ""
    verify_ssl: bool = True
    request_timeout_seconds: int = 30


@dataclass
class DeviceSetup:
    name: str = ""
    device_id: str = ""
    ip: str = ""
    port: int = 4370
    enabled: bool = True
    password: int = 0
    clear_from_device_on_fetch: bool = False


@dataclass
class SyncSetup:
    import_start_date: str = field(default_factory=lambda: datetime.date.today().isoformat())
    pull_frequency_minutes: int = 60


@dataclass
class SetupConfiguration:
    erpnext: ERPNextSetup = field(default_factory=ERPNextSetup)
    devices: list = field(default_factory=list)
    sync: SyncSetup = field(default_factory=SyncSetup)
    logging_level: str = "INFO"
    logging_retention_days: int = 30
