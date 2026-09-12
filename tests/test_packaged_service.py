import importlib
import os
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from manager import paths
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

    def test_packaged_logs_folder_uses_programdata_without_config(self):
        programdata = self.test_dir / "programdata"
        with mock.patch.object(paths, "is_frozen_app", return_value=True), mock.patch.object(paths, "PROGRAM_DATA_ROOT", programdata):
            self.assertEqual(paths.get_logs_folder(), programdata / "logs")

    def test_packaged_relative_config_log_path_resolves_under_programdata(self):
        config = types.SimpleNamespace(LOGS_DIRECTORY="logs")
        programdata = self.test_dir / "programdata"
        with mock.patch.object(paths, "is_frozen_app", return_value=True), mock.patch.object(paths, "PROGRAM_DATA_ROOT", programdata):
            self.assertEqual(paths.get_logs_folder(config), (programdata / "logs").resolve())

    def test_service_runtime_rewrites_relative_logs_directory_when_frozen(self):
        config_dir = self.test_dir / "config"
        programdata = self.test_dir / "programdata"
        config_dir.mkdir()
        (config_dir / "local_config.py").write_text("LOGS_DIRECTORY = 'logs'\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {"FPF_BIOMETRIC_CONFIG_DIR": str(config_dir)}), mock.patch.object(service_runtime, "PROGRAMDATA_ROOT", programdata), mock.patch.object(service_runtime, "is_frozen_runtime", return_value=True):
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
