"""Business logic for first-run setup independent of PyQt widgets."""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from config import paths as config_paths
from config.loader import load_json_config
from manager import diagnostics

from .validation import build_config_dict, safe_review_summary, validate_setup_config


class SetupController:
    def __init__(self, paths_module=config_paths, request_func=None, zk_class=None):
        self.paths = paths_module
        self.request_func = request_func
        self.zk_class = zk_class

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
        config_dict = validate_setup_config(setup_config)
        target = self.paths.get_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        self.paths.ensure_runtime_directories()
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(config_dict, handle, indent=2)
                handle.write("\n")
            load_json_config(temp_path, paths_module=self.paths)
            os.replace(str(temp_path), str(target))
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
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
