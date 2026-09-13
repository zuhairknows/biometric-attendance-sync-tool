import unittest

import version
from manager import paths


class WindowsInstallerDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.installer_text = (paths.PROJECT_ROOT / "installer" / "FPF-Biometric-Sync.iss").read_text(encoding="utf-8")
        self.build_script = (paths.PROJECT_ROOT / "build" / "build_release.ps1").read_text(encoding="utf-8")
        self.manager_spec = (paths.PROJECT_ROOT / "build" / "manager.spec").read_text(encoding="utf-8")

    def test_installer_uses_final_program_files_layout(self):
        self.assertIn("DefaultDirName={autopf}\\FPF Biometric Sync", self.installer_text)
        self.assertIn("..\\release\\FPF Biometric Sync\\*", self.installer_text)

    def test_installer_creates_programdata_directories(self):
        for folder in ["config", "logs", "state", "retry"]:
            self.assertIn("{commonappdata}\\FPF\\BiometricSync\\" + folder, self.installer_text)

    def test_installer_preserves_real_config_and_programdata(self):
        self.assertIn("local_config.py.template", self.installer_text)
        self.assertIn("onlyifdoesntexist", self.installer_text)
        self.assertIn("uninsneveruninstall", self.installer_text)
        self.assertNotIn('DestName: "local_config.py"', self.installer_text)
        self.assertNotIn("{commonappdata}\\FPF\\BiometricSync\"; Flags: delete", self.installer_text)

    def test_installer_generates_service_commands(self):
        self.assertIn("FPF-Biometric-Sync-Service.exe", self.installer_text)
        self.assertIn('Parameters: "install"', self.installer_text)
        self.assertIn('Parameters: "remove"', self.installer_text)
        self.assertIn("start= delayed-auto", self.installer_text)
        self.assertIn("restart/60000/restart/60000/restart/60000", self.installer_text)
        self.assertIn('Check: HasRealConfig', self.installer_text)

    def test_manager_spec_does_not_bundle_real_config(self):
        self.assertIn("'local_config'", self.manager_spec)
        self.assertNotIn("local_config.py'", self.manager_spec)

    def test_build_script_assembles_manager_and_service(self):
        self.assertIn("build\\service.spec", self.build_script)
        self.assertIn("build\\manager.spec", self.build_script)
        self.assertIn("PRODUCT_VERSION", self.build_script)
        self.assertIn("release", self.build_script)
        self.assertIn("service", self.build_script)
        self.assertIn("installer\\FPF-Biometric-Sync.iss", self.build_script)

    def test_product_version_is_consistent(self):
        self.assertEqual(version.PRODUCT_VERSION, "0.1.0")
        self.assertIn('#define AppVersion "0.1.0"', self.installer_text)
        self.assertIn("FPF-Biometric-Sync-Setup-0.1.0.exe", (paths.PROJECT_ROOT / "docs" / "installer.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
