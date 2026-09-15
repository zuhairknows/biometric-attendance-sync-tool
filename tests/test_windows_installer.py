import unittest

import version
from manager import paths


class WindowsInstallerDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.installer_path = paths.PROJECT_ROOT / "installer" / "Biometric-Attendance-Sync.iss"
        self.installer_text = self.installer_path.read_text(encoding="utf-8")
        self.build_script = (paths.PROJECT_ROOT / "build" / "build_release.ps1").read_text(encoding="utf-8")
        self.manager_spec = (paths.PROJECT_ROOT / "build" / "manager.spec").read_text(encoding="utf-8")
        self.service_spec = (paths.PROJECT_ROOT / "build" / "service.spec").read_text(encoding="utf-8")

    def test_installer_source_was_renamed(self):
        self.assertTrue(self.installer_path.exists())
        self.assertFalse((paths.PROJECT_ROOT / "installer" / "FPF-Biometric-Sync.iss").exists())

    def test_installer_uses_generic_product_identity(self):
        self.assertIn('#define AppName "Biometric Attendance Sync"', self.installer_text)
        self.assertIn('#define AppPublisher "Biometric Attendance Sync"', self.installer_text)
        self.assertIn("DefaultGroupName=Biometric Attendance Sync", self.installer_text)
        self.assertIn("Biometric Attendance Sync", self.installer_text)
        self.assertIn('Description: "Launch Biometric Attendance Sync"', self.installer_text)
        self.assertIn("Biometric Attendance Sync was installed", self.installer_text)
        self.assertIn("Open Biometric Attendance Sync and complete the setup wizard.", self.installer_text)
        self.assertNotIn("FPF Biometric Sync", self.installer_text)

    def test_installer_uses_final_program_files_layout(self):
        self.assertIn("DefaultDirName={autopf}\\Biometric Attendance Sync", self.installer_text)
        self.assertIn("..\\release\\Biometric Attendance Sync\\*", self.installer_text)

    def test_installer_creates_programdata_directories(self):
        for folder in ["config", "logs", "state", "retry", "secrets", "backups", "diagnostics"]:
            self.assertIn("{commonappdata}\\BiometricAttendanceSync\\" + folder, self.installer_text)

    def test_installer_preserves_real_config_and_programdata(self):
        # Commercial configuration must live outside Program Files so upgrades
        # replace application binaries without replacing customer configuration.
        self.assertIn(
            "{commonappdata}\\BiometricAttendanceSync",
            self.installer_text,
        )

        # The commercial Manager owns config.json. The installer must never
        # bundle, create, or overwrite a real customer configuration file.
        self.assertIn(
            "{commonappdata}\\BiometricAttendanceSync\\config.json",
            self.installer_text,
        )
        self.assertNotIn('DestName: "config.json"', self.installer_text)
        self.assertNotIn('Source: "..\\config.json"', self.installer_text)

        # The previous Python template is no longer part of the fresh-install
        # customer workflow.
        self.assertNotIn(
            "local_config.py.template",
            self.installer_text,
        )

        # Legacy local_config.py remains detectable only for upgrade/backward
        # compatibility. It must not be installed as a real customer config.
        self.assertIn(
            "{commonappdata}\\BiometricAttendanceSync\\config\\local_config.py",
            self.installer_text,
        )
        self.assertNotIn(
            'DestName: "local_config.py"',
            self.installer_text,
        )

        # Never delete the old customer ProgramData tree during installation or
        # upgrade. Legacy migration is handled by runtime compatibility logic.
        self.assertNotIn(
            "{commonappdata}\\FPF\\BiometricSync\"; Flags: delete",
            self.installer_text,
        )

        self.assertNotIn(
            "{commonappdata}\\BiometricAttendanceSync\"; Flags: delete",
            self.installer_text,
        )

    def test_installer_shortcuts_use_generic_product_name(self):
        self.assertIn('Name: "{group}\\Biometric Attendance Sync"', self.installer_text)
        self.assertIn('Name: "{autodesktop}\\Biometric Attendance Sync"', self.installer_text)
        self.assertNotIn('Name: "{group}\\Biometric Attendance Sync Manager"', self.installer_text)
        self.assertNotIn('Name: "{autodesktop}\\Biometric Attendance Sync Manager"', self.installer_text)

    def test_installer_docs_reflect_release_build_requirements(self):
        docs = (paths.PROJECT_ROOT / "docs" / "installer.md").read_text(encoding="utf-8")

        self.assertIn("build\\tools\\innosetup\\package\\tools\\ISCC.exe", docs)
        self.assertIn("Inno Setup 7", docs)
        self.assertIn("Inno Setup 6", docs)
        self.assertIn("the release build stops with a clear", docs)
        self.assertNotIn("staging folder is still created and the script exits with a warning", docs)

    def test_manual_acceptance_checklist_exists(self):
        checklist = (paths.PROJECT_ROOT / "docs" / "m5_6_manual_acceptance_checklist.md").read_text(encoding="utf-8")

        for scenario in [
            "Scenario A - Clean Install, No Configuration",
            "Scenario B - First-Run Configuration And Service Start",
            "Scenario C - Existing Configured Install Opens Dashboard",
            "Scenario D - Upgrade Preserves ProgramData",
            "Scenario E - Upgrade With Running Service",
            "Scenario F - Service Start Failure",
            "Scenario G - Shortcuts And Product Naming",
            "Scenario H - Uninstall Preserves Customer Data",
            "Scenario I - Reinstall After Uninstall",
        ]:
            self.assertIn(scenario, checklist)

        self.assertIn("Result: Pass / Fail / N/A", checklist)
        self.assertIn("C:\\ProgramData\\BiometricAttendanceSync", checklist)

    def test_installer_generates_service_commands(self):
        self.assertIn("Biometric-Attendance-Sync-Service.exe", self.installer_text)
        self.assertIn("ERPNextBiometricPushService", self.installer_text)
        self.assertIn('Parameters: "install"', self.installer_text)
        self.assertIn('Parameters: "remove"', self.installer_text)
        self.assertIn("start= delayed-auto", self.installer_text)
        self.assertIn("restart/60000/restart/60000/restart/60000", self.installer_text)
        self.assertIn('Check: HasRealConfig', self.installer_text)

    def test_installer_preserves_upgrade_identifiers(self):
        self.assertIn("AppId={{F3E3DD71-B7E0-4CF5-A1BF-25F100100100}", self.installer_text)
        self.assertIn('#define ServiceName "ERPNextBiometricPushService"', self.installer_text)

    def test_manager_spec_does_not_bundle_real_config(self):
        self.assertIn("'local_config'", self.manager_spec)
        self.assertNotIn("local_config.py'", self.manager_spec)

    def test_pyinstaller_specs_use_generic_executable_names(self):
        self.assertIn("name='Biometric-Attendance-Sync-Manager'", self.manager_spec)
        self.assertIn("name='Biometric-Attendance-Sync-Service'", self.service_spec)
        self.assertNotIn("name='FPF-Biometric-Sync-Manager'", self.manager_spec)
        self.assertNotIn("name='FPF-Biometric-Sync-Service'", self.service_spec)

    def test_build_script_assembles_manager_and_service(self):
        self.assertIn("build\\service.spec", self.build_script)
        self.assertIn("build\\manager.spec", self.build_script)
        self.assertIn("PRODUCT_VERSION", self.build_script)

        self.assertIn(
            '$releaseRoot = Join-Path $repoRoot "release"',
            self.build_script,
        )
        self.assertIn(
            '$installerBuildDir = Join-Path $releaseRoot "installer"',
            self.build_script,
        )

        self.assertIn("Remove-WorkspacePath", self.build_script)
        self.assertIn(
            "dist\\Biometric-Attendance-Sync-Service",
            self.build_script,
        )
        self.assertIn(
            "dist\\Biometric-Attendance-Sync-Manager",
            self.build_script,
        )
        self.assertIn(
            "dist\\FPF-Biometric-Sync-Service",
            self.build_script,
        )
        self.assertIn(
            "dist\\FPF-Biometric-Sync-Manager",
            self.build_script,
        )

        self.assertIn("Resolve-InnoCompiler", self.build_script)
        self.assertIn(
            "build\\tools\\innosetup\\package\\tools\\ISCC.exe",
            self.build_script,
        )

        self.assertIn(
            "Biometric-Attendance-Sync-Manager.exe",
            self.build_script,
        )
        self.assertIn(
            "Biometric-Attendance-Sync-Service.exe",
            self.build_script,
        )

        self.assertIn(
            "installer\\Biometric-Attendance-Sync.iss",
            self.build_script,
        )

        self.assertIn(
            "unittest discover -s tests -v",
            self.build_script,
        )

        self.assertIn("SHA256SUMS.txt", self.build_script)
        self.assertIn("RELEASE_NOTES.md", self.build_script)
        self.assertIn("Get-FileHash", self.build_script)
        self.assertIn("RELEASE BUILD SUCCESSFUL", self.build_script)

    def test_product_version_is_consistent(self):
        self.assertEqual(version.PRODUCT_VERSION, "0.1.0")
        self.assertIn('#define AppVersion "0.1.0"', self.installer_text)
        self.assertIn("OutputBaseFilename=Biometric-Attendance-Sync-Setup-{#AppVersion}", self.installer_text)


if __name__ == "__main__":
    unittest.main()
