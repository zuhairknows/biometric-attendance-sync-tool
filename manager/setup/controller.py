"""Business logic for first-run setup independent of PyQt widgets."""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from config import paths as config_paths
from config.loader import load_json_config
from config.secrets import (
    SecretNotFound,
    create_secret_store,
    device_password_secret_id,
    erpnext_api_key_secret_id,
    erpnext_api_secret_secret_id,
)
from manager import diagnostics

from .model import DeviceSetup, ERPNextSetup, SetupConfiguration, SyncSetup
from .validation import build_config_dict, safe_review_summary, validate_setup_config


class SetupController:
    def __init__(self, paths_module=config_paths, request_func=None, zk_class=None, secret_store=None):
        self.paths = paths_module
        self.request_func = request_func
        self.zk_class = zk_class
        self.secret_store = secret_store

    def validate(self, setup_config):
        return validate_setup_config(setup_config)

    def validate_erpnext(self, setup_config):
        draft = self._complete_minimum_config(setup_config)
        draft.devices = [
            SimpleNamespace(
                name="Validation Device",
                device_id="VALIDATION_DEVICE",
                ip="127.0.0.1",
                port=4370,
                enabled=False,
                password=0,
                clear_from_device_on_fetch=False,
            )
        ]
        return validate_setup_config(draft)

    def review_summary(self, setup_config):
        return safe_review_summary(setup_config)

    def load_existing_setup_config(self):
        existing = self._read_existing_config()
        if not existing:
            return SetupConfiguration(devices=[DeviceSetup()])
        erpnext = existing.get("erpnext", {}) if isinstance(existing.get("erpnext"), dict) else {}
        sync = existing.get("sync", {}) if isinstance(existing.get("sync"), dict) else {}
        logging_config = existing.get("logging", {}) if isinstance(existing.get("logging"), dict) else {}
        devices = []
        for device in existing.get("devices", []) if isinstance(existing.get("devices"), list) else []:
            if not isinstance(device, dict):
                continue
            devices.append(DeviceSetup(
                name=str(device.get("name") or ""),
                device_id=str(device.get("device_id") or ""),
                ip=str(device.get("ip") or device.get("host") or ""),
                port=int(device.get("port", 4370)),
                enabled=bool(device.get("enabled", True)),
                password="",
                has_existing_password=_device_has_existing_password(device),
                clear_from_device_on_fetch=bool(device.get("clear_from_device_on_fetch", False)),
            ))
        return SetupConfiguration(
            erpnext=ERPNextSetup(
                url=str(erpnext.get("url") or ""),
                api_key="",
                api_secret="",
                has_existing_api_key=bool(str(erpnext.get("api_key") or erpnext.get("api_user") or "").strip() or erpnext.get("api_key_ref")),
                has_existing_api_secret=bool(str(erpnext.get("api_secret") or "").strip() or erpnext.get("api_secret_ref")),
                verify_ssl=bool(erpnext.get("verify_ssl", True)),
                request_timeout_seconds=int(erpnext.get("request_timeout_seconds", 30)),
            ),
            devices=devices or [DeviceSetup()],
            sync=SyncSetup(
                import_start_date=str(sync.get("import_start_date") or ""),
                pull_frequency_minutes=int(sync.get("pull_frequency_minutes", 60)),
            ),
            logging_level=str(logging_config.get("level", "INFO")),
            logging_retention_days=int(logging_config.get("retention_days", 30)),
        )

    def test_erpnext(self, setup_config):
        runtime = self._runtime_from_setup(setup_config)
        return diagnostics.test_erpnext_connection(runtime, request_func=self.request_func)

    def test_devices(self, setup_config, sync_module):
        runtime = self._runtime_from_setup(setup_config)
        enabled_devices = [device for device in runtime.devices if device.get("enabled", True)]
        runtime.devices = enabled_devices
        if not enabled_devices:
            return diagnostics.DiagnosticResult(True, "ok", "No enabled devices to test.")
        return diagnostics.test_devices(runtime, sync_module=sync_module, zk_class=self.zk_class)

    def write_config_atomic(self, setup_config):
        target = self.paths.get_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        self.paths.ensure_runtime_directories()
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
        temp_path = Path(temp_name)
        secret_snapshot = self._snapshot_secret_files()
        try:
            config_dict = build_config_dict(setup_config)
            config_dict = self._protect_config_secrets(config_dict)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(config_dict, handle, indent=2)
                handle.write("\n")
            load_json_config(temp_path, paths_module=self.paths, secret_store=self._secret_store())
            self._replace_config_file(temp_path, target)
            self._cleanup_unused_device_passwords(config_dict)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if temp_path.exists():
                temp_path.unlink()
            self._restore_secret_files(secret_snapshot)
            raise
        return target

    def _runtime_from_setup(self, setup_config):
        config_dict = build_config_dict(setup_config)
        return SimpleNamespace(
            ERPNEXT_URL=config_dict["erpnext"]["url"],
            ERPNEXT_API_KEY=config_dict["erpnext"]["api_key"],
            ERPNEXT_API_SECRET=config_dict["erpnext"]["api_secret"],
            ERPNEXT_VERIFY_SSL=config_dict["erpnext"]["verify_ssl"],
            ERPNEXT_REQUEST_TIMEOUT=config_dict["erpnext"]["request_timeout_seconds"],
            REQUEST_TIMEOUT=config_dict["erpnext"]["request_timeout_seconds"],
            LOGS_DIRECTORY=str(self.paths.get_logs_dir()),
            devices=config_dict["devices"],
        )

    def _complete_minimum_config(self, setup_config):
        return SimpleNamespace(
            erpnext=setup_config.erpnext,
            devices=list(setup_config.devices),
            sync=setup_config.sync,
            logging_level=setup_config.logging_level,
            logging_retention_days=setup_config.logging_retention_days,
        )

    def _secret_store(self):
        return self.secret_store or create_secret_store(paths_module=self.paths)

    def _protect_config_secrets(self, config_dict):
        existing = self._read_existing_config()
        protected = json.loads(json.dumps(config_dict))
        store = self._secret_store()

        erpnext = protected["erpnext"]
        existing_erpnext = existing.get("erpnext", {}) if isinstance(existing.get("erpnext"), dict) else {}
        self._write_or_keep_secret(
            erpnext,
            existing_erpnext,
            "api_key",
            "api_key_ref",
            erpnext_api_key_secret_id(),
            store,
        )
        self._write_or_keep_secret(
            erpnext,
            existing_erpnext,
            "api_secret",
            "api_secret_ref",
            erpnext_api_secret_secret_id(),
            store,
        )

        existing_devices = {}
        for device in existing.get("devices", []) if isinstance(existing.get("devices"), list) else []:
            if isinstance(device, dict):
                existing_devices[str(device.get("device_id") or "")] = device

        for device in protected["devices"]:
            existing_device = existing_devices.get(str(device.get("device_id") or ""), {})
            self._write_or_keep_secret(
                device,
                existing_device,
                "password",
                "password_ref",
                device_password_secret_id(device["device_id"]),
                store,
                keep_zero=True,
            )
        return protected

    def _write_or_keep_secret(self, section, existing_section, value_key, ref_key, default_ref, store, keep_zero=False):
        value = section.pop(value_key, "")
        blank_value = value in (None, "")
        if keep_zero:
            blank_value = value in (None, "")
        if blank_value:
            existing_ref = existing_section.get(ref_key)
            if existing_ref and store.secret_exists(existing_ref):
                section[ref_key] = existing_ref
                return
            if value_key in existing_section and str(existing_section.get(value_key) or "").strip():
                if keep_zero and int(existing_section[value_key]) == 0:
                    return
                store.set_secret(default_ref, existing_section[value_key])
                section[ref_key] = default_ref
                return
            if keep_zero:
                return
            else:
                raise SecretNotFound("Existing secret is missing for " + ref_key)
        if keep_zero and int(value) == 0:
            return
        store.set_secret(default_ref, value)
        section[ref_key] = default_ref

    def _read_existing_config(self):
        path = self.paths.get_config_path()
        if not path.is_file():
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _cleanup_unused_device_passwords(self, config_dict):
        used_refs = {
            device.get("password_ref")
            for device in config_dict.get("devices", [])
            if isinstance(device, dict) and device.get("password_ref")
        }
        for secret_path in self.paths.get_secrets_dir().glob("devices/*/password.secret"):
            device_id = secret_path.parent.name
            ref = device_password_secret_id(device_id)
            if ref not in used_refs:
                self._secret_store().delete_secret(ref)

    def _snapshot_secret_files(self):
        secrets_dir = self.paths.get_secrets_dir()
        snapshot = {}
        if not secrets_dir.exists():
            return snapshot
        for path in secrets_dir.rglob("*.secret"):
            if path.is_file():
                snapshot[path.relative_to(secrets_dir)] = path.read_bytes()
        return snapshot

    def _restore_secret_files(self, snapshot):
        secrets_dir = self.paths.get_secrets_dir()
        if secrets_dir.exists():
            for path in list(secrets_dir.rglob("*.secret")):
                path.unlink()
        for relative_path, contents in snapshot.items():
            path = secrets_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)

    def _replace_config_file(self, temp_path, target):
        os.replace(str(temp_path), str(target))


def _device_has_existing_password(device):
    if device.get("password_ref"):
        return True
    if "password" not in device:
        return False
    try:
        return int(device.get("password") or 0) != 0
    except (TypeError, ValueError):
        return bool(str(device.get("password") or "").strip())
