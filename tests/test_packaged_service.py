import importlib
import os
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from manager import paths
import runtime_paths
import service_runtime


class PackagedServicePathTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.original_path = list(sys.path)
        self.test_dir = Path.cwd() / ".test-logs" / self._testMethodName
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True)
        sys.modules.pop("local_config", None)

    def tearDown(self):
        os.chdir(self.original_cwd)
        sys.path[:] = self.original_path
        sys.modules.pop("local_config", None)
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_development_runtime_path_detection(self):
        with mock.patch.object(paths, "is_frozen_app", return_value=False):
            self.assertEqual(paths.get_app_root(), paths.PROJECT_ROOT)

    def test_packaged_runtime_path_detection_from_manager_directory(self):
        service_exe = self.test_dir / paths.SERVICE_EXE_NAME
        service_exe.write_text("placeholder", encoding="utf-8")

        with mock.patch.object(paths, "is_frozen_app", return_value=True), mock.patch.object(paths, "get_app_root", return_value=self.test_dir):
            self.assertEqual(paths.get_packaged_service_executable(), service_exe.resolve())

    def test_legacy_packaged_service_exe_is_detected_when_new_exe_is_absent(self):
        legacy_exe = self.test_dir / paths.LEGACY_SERVICE_EXE_NAME
        legacy_exe.write_text("placeholder", encoding="utf-8")

        with mock.patch.object(paths, "is_frozen_app", return_value=True), mock.patch.object(paths, "get_app_root", return_value=self.test_dir):
            runtime = paths.resolve_service_runtime("python.exe")

        self.assertEqual(runtime.runtime_type, "packaged")
        self.assertEqual(runtime.source, "legacy-packaged-manager")
        self.assertEqual(runtime.executable, legacy_exe.resolve())

    def test_generic_packaged_service_exe_wins_over_legacy_exe(self):
        service_exe = self.test_dir / paths.SERVICE_EXE_NAME
        service_exe.write_text("placeholder", encoding="utf-8")
        legacy_exe = self.test_dir / paths.LEGACY_SERVICE_EXE_NAME
        legacy_exe.write_text("placeholder", encoding="utf-8")

        with mock.patch.object(paths, "is_frozen_app", return_value=True), mock.patch.object(paths, "get_app_root", return_value=self.test_dir):
            runtime = paths.resolve_service_runtime("python.exe")

        self.assertEqual(runtime.source, "packaged-manager")
        self.assertEqual(runtime.executable, service_exe.resolve())

    def test_explicit_service_exe_env_override(self):
        service_exe = self.test_dir / "custom-service.exe"
        service_exe.write_text("placeholder", encoding="utf-8")

        with mock.patch.dict(os.environ, {"FPF_BIOMETRIC_SERVICE_EXE": str(service_exe)}, clear=True):
            runtime = paths.resolve_service_runtime("python.exe")

        self.assertEqual(runtime.runtime_type, "packaged")
        self.assertEqual(runtime.executable, service_exe.resolve())
        self.assertEqual(runtime.command_prefix, (str(service_exe.resolve()),))

    def test_new_service_exe_env_override(self):
        service_exe = self.test_dir / "new-service.exe"
        service_exe.write_text("placeholder", encoding="utf-8")

        with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_SERVICE_EXE": str(service_exe)}, clear=True):
            runtime = paths.resolve_service_runtime("python.exe")

        self.assertEqual(runtime.runtime_type, "packaged")
        self.assertEqual(runtime.executable, service_exe.resolve())
        self.assertEqual(runtime.command_prefix, (str(service_exe.resolve()),))

    def test_new_service_exe_env_takes_priority_over_legacy(self):
        legacy_exe = self.test_dir / "legacy-service.exe"
        legacy_exe.write_text("placeholder", encoding="utf-8")
        new_exe = self.test_dir / "new-service.exe"
        new_exe.write_text("placeholder", encoding="utf-8")

        with mock.patch.dict(os.environ, {"FPF_BIOMETRIC_SERVICE_EXE": str(legacy_exe), "BIOMETRIC_SYNC_SERVICE_EXE": str(new_exe)}, clear=True):
            runtime = paths.resolve_service_runtime("python.exe")

        self.assertEqual(runtime.executable, new_exe.resolve())

    def test_packaged_manager_service_subfolder_resolution(self):
        service_folder = self.test_dir / "service"
        service_folder.mkdir()
        service_exe = service_folder / paths.SERVICE_EXE_NAME
        service_exe.write_text("placeholder", encoding="utf-8")

        with mock.patch.object(paths, "is_frozen_app", return_value=True), mock.patch.object(paths, "get_app_root", return_value=self.test_dir):
            runtime = paths.resolve_service_runtime("python.exe")

        self.assertEqual(runtime.runtime_type, "packaged")
        self.assertEqual(runtime.source, "packaged-manager")
        self.assertEqual(runtime.executable, service_exe.resolve())

    def test_old_customer_test_service_fallback_removed(self):
        old_fallback_name = "FP" + "1_TEST_SERVICE_EXE"
        self.assertFalse(hasattr(paths, old_fallback_name))

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(paths, "is_frozen_app", return_value=False), mock.patch.object(paths, "get_app_root", return_value=self.test_dir):
            runtime = paths.resolve_service_runtime("python.exe")

        self.assertEqual(runtime.runtime_type, "development")

    def test_development_service_runtime_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(paths, "is_frozen_app", return_value=False), mock.patch.object(paths, "get_app_root", return_value=self.test_dir):
            runtime = paths.resolve_service_runtime("python.exe")

        self.assertEqual(runtime.runtime_type, "development")
        self.assertEqual(runtime.command_prefix, ("python.exe", str(paths.SERVICE_SCRIPT)))

    def test_default_programdata_root_is_generic(self):
        new_root = self.test_dir / "new-programdata"
        absent_legacy = self.test_dir / "absent-legacy"

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(runtime_paths, "DEFAULT_PROGRAMDATA_ROOT", new_root), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", absent_legacy):
            self.assertEqual(runtime_paths.resolve_programdata_root(), new_root)
            self.assertEqual(runtime_paths.resolve_active_programdata_root(), new_root)

        self.assertEqual(str(runtime_paths.DEFAULT_PROGRAMDATA_ROOT), r"C:\ProgramData\BiometricAttendanceSync")
        self.assertEqual(str(runtime_paths.LEGACY_PROGRAMDATA_ROOT), r"C:\ProgramData\FPF\BiometricSync")

    def test_new_programdata_env_overrides_default(self):
        custom_root = self.test_dir / "custom-programdata"

        with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_PROGRAMDATA": str(custom_root)}, clear=True):
            self.assertEqual(runtime_paths.resolve_programdata_root(), custom_root.resolve())

    def test_legacy_programdata_env_still_honored(self):
        legacy_root = self.test_dir / "legacy-env-programdata"

        with mock.patch.dict(os.environ, {"FPF_BIOMETRIC_PROGRAMDATA": str(legacy_root)}, clear=True):
            self.assertEqual(runtime_paths.resolve_programdata_root(), legacy_root.resolve())

    def test_new_programdata_env_takes_priority_over_legacy(self):
        legacy_root = self.test_dir / "legacy-env-programdata"
        new_root = self.test_dir / "new-env-programdata"

        with mock.patch.dict(os.environ, {"FPF_BIOMETRIC_PROGRAMDATA": str(legacy_root), "BIOMETRIC_SYNC_PROGRAMDATA": str(new_root)}, clear=True):
            self.assertEqual(runtime_paths.resolve_programdata_root(), new_root.resolve())

    def test_explicit_programdata_env_does_not_fall_back_to_legacy(self):
        explicit_root = self.test_dir / "explicit-programdata"
        legacy_root = self.test_dir / "legacy-programdata"
        legacy_config = legacy_root / "config"
        legacy_config.mkdir(parents=True)
        (legacy_config / "local_config.py").write_text("# legacy config\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_PROGRAMDATA": str(explicit_root)}, clear=True), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", legacy_root), mock.patch.object(paths, "is_frozen_app", return_value=True):
            self.assertEqual(paths.get_config_folder(), explicit_root / "config")
            self.assertEqual(paths.get_logs_folder(), explicit_root / "logs")

    def test_explicit_legacy_programdata_env_is_authoritative(self):
        override_root = self.test_dir / "legacy-override-root"

        with mock.patch.dict(os.environ, {"FPF_BIOMETRIC_PROGRAMDATA": str(override_root)}, clear=True), mock.patch.object(runtime_paths, "DEFAULT_PROGRAMDATA_ROOT", self.test_dir / "absent-new"), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", self.test_dir / "absent-legacy"), mock.patch.object(paths, "is_frozen_app", return_value=True):
            self.assertEqual(paths.get_config_folder(), override_root / "config")

    def test_whitespace_programdata_env_is_treated_as_unset(self):
        new_root = self.test_dir / "new-programdata"
        legacy_root = self.test_dir / "legacy-programdata"
        legacy_config = legacy_root / "config"
        legacy_config.mkdir(parents=True)
        (legacy_config / "local_config.py").write_text("# legacy config\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_PROGRAMDATA": "   "}, clear=True), mock.patch.object(runtime_paths, "DEFAULT_PROGRAMDATA_ROOT", new_root), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", legacy_root), mock.patch.object(paths, "is_frozen_app", return_value=True):
            self.assertEqual(paths.get_config_folder(), legacy_root / "config")

    def test_empty_config_dir_env_is_treated_as_unset(self):
        new_root = self.test_dir / "new-programdata"
        legacy_root = self.test_dir / "legacy-programdata"
        legacy_config = legacy_root / "config"
        legacy_config.mkdir(parents=True)
        (legacy_config / "local_config.py").write_text("# legacy config\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_CONFIG_DIR": ""}, clear=True), mock.patch.object(runtime_paths, "DEFAULT_PROGRAMDATA_ROOT", new_root), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", legacy_root), mock.patch.object(paths, "is_frozen_app", return_value=True):
            self.assertEqual(paths.get_config_folder(), legacy_root / "config")

    def test_programdata_config_path_in_packaged_mode(self):
        programdata = self.test_dir / "programdata"
        absent_legacy = self.test_dir / "absent-legacy"

        with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_PROGRAMDATA": str(programdata)}, clear=True), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", absent_legacy), mock.patch.object(paths, "is_frozen_app", return_value=True):
            self.assertEqual(paths.get_config_folder(), programdata / "config")

    def test_legacy_programdata_env_still_honored_in_packaged_mode(self):
        programdata = self.test_dir / "legacy-env-programdata"
        absent_legacy = self.test_dir / "absent-legacy"

        with mock.patch.dict(os.environ, {"FPF_BIOMETRIC_PROGRAMDATA": str(programdata)}, clear=True), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", absent_legacy), mock.patch.object(paths, "is_frozen_app", return_value=True):
            self.assertEqual(paths.get_config_folder(), programdata / "config")

    def test_config_dir_env_override(self):
        config_dir = self.test_dir / "custom-config"

        with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_CONFIG_DIR": str(config_dir)}, clear=True):
            self.assertEqual(paths.get_config_folder(), config_dir.resolve())

    def test_legacy_config_dir_env_still_honored(self):
        config_dir = self.test_dir / "legacy-config"

        with mock.patch.dict(os.environ, {"FPF_BIOMETRIC_CONFIG_DIR": str(config_dir)}, clear=True):
            self.assertEqual(paths.get_config_folder(), config_dir.resolve())

    def test_new_config_dir_env_takes_priority_over_legacy(self):
        legacy_config_dir = self.test_dir / "legacy-config"
        new_config_dir = self.test_dir / "new-config"

        with mock.patch.dict(os.environ, {"FPF_BIOMETRIC_CONFIG_DIR": str(legacy_config_dir), "BIOMETRIC_SYNC_CONFIG_DIR": str(new_config_dir)}, clear=True):
            self.assertEqual(paths.get_config_folder(), new_config_dir.resolve())

    def test_packaged_mode_without_any_config_uses_generic_programdata(self):
        new_root = self.test_dir / "new-programdata"
        absent_legacy = self.test_dir / "absent-legacy"

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(runtime_paths, "DEFAULT_PROGRAMDATA_ROOT", new_root), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", absent_legacy), mock.patch.object(paths, "is_frozen_app", return_value=True):
            self.assertEqual(paths.get_config_folder(), new_root / "config")

    def test_packaged_mode_discovers_legacy_config_when_new_config_missing(self):
        new_root = self.test_dir / "new-programdata"
        legacy_root = self.test_dir / "legacy-programdata"
        legacy_config = legacy_root / "config"
        legacy_config.mkdir(parents=True)
        (legacy_config / "local_config.py").write_text("# legacy config\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(runtime_paths, "DEFAULT_PROGRAMDATA_ROOT", new_root), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", legacy_root), mock.patch.object(paths, "is_frozen_app", return_value=True):
            self.assertEqual(paths.get_config_folder(), legacy_root / "config")

    def test_packaged_mode_prefers_new_config_when_both_exist(self):
        new_root = self.test_dir / "new-programdata"
        legacy_root = self.test_dir / "legacy-programdata"
        for root in (new_root, legacy_root):
            config = root / "config"
            config.mkdir(parents=True)
            (config / "local_config.py").write_text("# config\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(runtime_paths, "DEFAULT_PROGRAMDATA_ROOT", new_root), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", legacy_root), mock.patch.object(paths, "is_frozen_app", return_value=True):
            self.assertEqual(paths.get_config_folder(), new_root / "config")

    def test_service_runtime_uses_same_legacy_config_discovery(self):
        new_root = self.test_dir / "new-programdata"
        legacy_root = self.test_dir / "legacy-programdata"
        legacy_config = legacy_root / "config"
        legacy_config.mkdir(parents=True)
        (legacy_config / "local_config.py").write_text("# legacy config\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(runtime_paths, "DEFAULT_PROGRAMDATA_ROOT", new_root), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", legacy_root), mock.patch.object(service_runtime, "is_frozen_runtime", return_value=True):
            self.assertEqual(service_runtime.get_external_config_dir(), legacy_root / "config")

    def test_packaged_logs_folder_uses_programdata_without_config(self):
        programdata = self.test_dir / "programdata"
        absent_legacy = self.test_dir / "absent-legacy"

        with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_PROGRAMDATA": str(programdata)}, clear=True), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", absent_legacy), mock.patch.object(paths, "is_frozen_app", return_value=True):
            self.assertEqual(paths.get_logs_folder(), programdata / "logs")

    def test_packaged_relative_config_log_path_resolves_under_programdata(self):
        config = types.SimpleNamespace(LOGS_DIRECTORY="logs")
        programdata = self.test_dir / "programdata"
        absent_legacy = self.test_dir / "absent-legacy"

        with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_PROGRAMDATA": str(programdata)}, clear=True), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", absent_legacy), mock.patch.object(paths, "is_frozen_app", return_value=True):
            self.assertEqual(paths.get_logs_folder(config), (programdata / "logs").resolve())

    def test_service_runtime_rewrites_relative_logs_directory_when_frozen(self):
        config_dir = self.test_dir / "config"
        programdata = self.test_dir / "programdata"
        absent_legacy = self.test_dir / "absent-legacy"
        config_dir.mkdir()
        (config_dir / "local_config.py").write_text("LOGS_DIRECTORY = 'logs'\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_CONFIG_DIR": str(config_dir), "BIOMETRIC_SYNC_PROGRAMDATA": str(programdata)}, clear=True), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", absent_legacy), mock.patch.object(service_runtime, "is_frozen_runtime", return_value=True):
            service_runtime.prepare_runtime_paths()
            config = service_runtime.load_runtime_config()

        self.assertEqual(config.LOGS_DIRECTORY, str(programdata / "logs"))

    def test_pyinstaller_spec_does_not_bundle_local_config_credentials(self):
        spec_text = (paths.PROJECT_ROOT / "build" / "service.spec").read_text(encoding="utf-8")

        self.assertIn("'../local_config.py.template'", spec_text)
        self.assertNotIn("'../local_config.py'", spec_text)
        self.assertIn("'local_config'", spec_text)


if __name__ == "__main__":
    unittest.main()
