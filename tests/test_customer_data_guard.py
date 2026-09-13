import os
import re
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SELF_RELATIVE_PATH = Path("tests/test_customer_data_guard.py")

TEXT_EXTENSIONS = {
    ".cfg",
    ".ini",
    ".iss",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".spec",
    ".template",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

TEXT_FILENAMES = {
    ".gitignore",
    "LICENSE",
}

EXCLUDED_DIRECTORIES = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "release",
}

FORBIDDEN_CUSTOMER_TERMS = {
    "myfpf",
    "erp.myfpf.com",
    "FP1_DEVICE_01",
    "FP1_DEVICE_02",
    "10.0.0.20",
    r"C:\FPF-Test",
    "uFace800",
    "صباحي 1",
    "Shift Type ليلي",
}

FORBIDDEN_FILENAME_TERMS = {
    "FP1",
    "myfpf",
}

FORBIDDEN_TRACKED_FILENAMES = {
    "local_config.py",
    ".env",
    "installer/FPF-Biometric-Sync.iss",
}

SENSITIVE_FILENAME_SUFFIXES = {
    ".pem",
    ".key",
}

ALLOWED_LEGACY_REFERENCE_FILES = {
    "README.md",
    "build/build_release.ps1",
    "docs/installer.md",
    "docs/packaged-service.md",
    "docs/production-checklist.md",
    "docs/sync-manager.md",
    "manager/paths.py",
    "runtime_paths.py",
    "tests/test_packaged_service.py",
    "tests/test_windows_installer.py",
}

ALLOWED_LEGACY_REFERENCES = {
    "FPF_BIOMETRIC_PROGRAMDATA",
    "FPF_BIOMETRIC_CONFIG_DIR",
    "FPF_BIOMETRIC_SERVICE_EXE",
    "FPF_BIOMETRIC_*",
    r"C:\ProgramData\FPF\BiometricSync",
    "{commonappdata}\\FPF\\BiometricSync",
    r"{commonappdata}\\FPF\\BiometricSync",
    "FPF-Biometric-Sync-Service.exe",
    "FPF-Biometric-Sync-Service",
    "FPF-Biometric-Sync-Manager",
    "FPF-Biometric-Sync-Setup",
    "FPF-Biometric-Sync.iss",
    "FPF Biometric Sync",
}

PLACEHOLDER_CREDENTIAL_VALUES = {
    "",
    "YOUR_API_KEY",
    "YOUR_API_SECRET",
    "your-api-key",
    "your-api-secret",
    "CHANGE_ME",
    "changeme",
}

CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"^\s*(ERPNEXT_API_KEY|ERPNEXT_API_SECRET)\s*=\s*['\"]([^'\"]*)['\"]",
    re.MULTILINE,
)


def normalize_relative_path(path):
    return Path(path).as_posix()


def tracked_files():
    try:
        completed = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return recursive_repository_files()

    return [
        Path(line)
        for line in completed.stdout.splitlines()
        if line.strip()
    ]


def recursive_repository_files():
    files = []
    for root, dirs, filenames in os.walk(PROJECT_ROOT):
        dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRECTORIES]
        for filename in filenames:
            path = Path(root, filename).relative_to(PROJECT_ROOT)
            files.append(path)
    return files


def is_text_like(path):
    return path.suffix in TEXT_EXTENSIONS or path.name in TEXT_FILENAMES


def read_text(path):
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def is_configuration_file(path):
    relative_path = normalize_relative_path(path)
    return (
        path.name == "local_config.py.template"
        or path.name == "local_config.py"
        or path.name == ".env"
        or relative_path.endswith(".env")
    )


def find_forbidden_customer_terms(path, text):
    if path == SELF_RELATIVE_PATH:
        return []

    return [
        f"Forbidden customer reference {term!r} found in {normalize_relative_path(path)}"
        for term in FORBIDDEN_CUSTOMER_TERMS
        if term in text
    ]


def find_unexpected_legacy_references(path, text):
    if path == SELF_RELATIVE_PATH or "FPF" not in text:
        return []

    relative_path = normalize_relative_path(path)
    findings = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if "FPF" not in line:
            continue
        if relative_path not in ALLOWED_LEGACY_REFERENCE_FILES:
            findings.append(
                f"Unexpected legacy FPF reference found in {relative_path}:{line_number}"
            )
            continue
        if not any(reference in line for reference in ALLOWED_LEGACY_REFERENCES):
            findings.append(
                f"Unexpected legacy FPF reference found in {relative_path}:{line_number}"
            )
    return findings


def find_potential_credentials(path, text):
    if path == SELF_RELATIVE_PATH:
        return []
    if not is_configuration_file(path):
        return []

    findings = []
    for _, value in CREDENTIAL_ASSIGNMENT_RE.findall(text):
        if value not in PLACEHOLDER_CREDENTIAL_VALUES:
            findings.append(
                f"Potential real ERPNext API credential detected in {normalize_relative_path(path)}"
            )
            break
    return findings


def find_filename_issues(path):
    relative_path = normalize_relative_path(path)
    findings = []

    if relative_path in FORBIDDEN_TRACKED_FILENAMES:
        findings.append(f"Sensitive or retired file must not be tracked: {relative_path}")

    if path.suffix in SENSITIVE_FILENAME_SUFFIXES:
        findings.append(f"Sensitive key/certificate file must not be tracked: {relative_path}")

    for term in FORBIDDEN_FILENAME_TERMS:
        if term in relative_path:
            findings.append(f"Forbidden customer identifier {term!r} found in tracked filename {relative_path}")

    return findings


class CustomerDataGuardTests(unittest.TestCase):
    def test_tracked_text_files_do_not_contain_customer_data(self):
        findings = []
        for path in tracked_files():
            if not is_text_like(path):
                continue
            text = read_text(path)
            findings.extend(find_forbidden_customer_terms(path, text))
            findings.extend(find_unexpected_legacy_references(path, text))
            findings.extend(find_potential_credentials(path, text))

        self.assertEqual([], findings)

    def test_tracked_filenames_do_not_contain_customer_data_or_secrets(self):
        findings = []
        for path in tracked_files():
            findings.extend(find_filename_issues(path))

        self.assertEqual([], findings)

    def test_local_config_is_not_tracked(self):
        tracked = {normalize_relative_path(path) for path in tracked_files()}

        self.assertNotIn("local_config.py", tracked)

    def test_guard_detects_known_customer_terms(self):
        sample_path = Path("docs/example.md")
        sample_text = "erp.myfpf.com\nFP1_DEVICE_01\n10.0.0.20\n"

        findings = find_forbidden_customer_terms(sample_path, sample_text)

        self.assertIn("Forbidden customer reference 'erp.myfpf.com' found in docs/example.md", findings)
        self.assertIn("Forbidden customer reference 'FP1_DEVICE_01' found in docs/example.md", findings)
        self.assertIn("Forbidden customer reference '10.0.0.20' found in docs/example.md", findings)

    def test_guard_detects_unexpected_fpf_references(self):
        findings = find_unexpected_legacy_references(Path("manager/app.py"), "FPF_BIOMETRIC_SERVICE_EXE\n")

        self.assertEqual(["Unexpected legacy FPF reference found in manager/app.py:1"], findings)

    def test_guard_allows_documented_legacy_references_in_allowlisted_files(self):
        text = "FPF_BIOMETRIC_SERVICE_EXE\nC:\\ProgramData\\FPF\\BiometricSync\n"

        findings = find_unexpected_legacy_references(Path("runtime_paths.py"), text)

        self.assertEqual([], findings)

    def test_credential_guard_allows_placeholders_and_blocks_real_values(self):
        template_text = "ERPNEXT_API_KEY = 'YOUR_API_KEY'\nERPNEXT_API_SECRET = 'YOUR_API_SECRET'\n"
        real_text = "ERPNEXT_API_KEY = 'real-looking-key'\nERPNEXT_API_SECRET = 'real-looking-secret'\n"

        self.assertEqual([], find_potential_credentials(Path("local_config.py.template"), template_text))
        self.assertEqual(
            ["Potential real ERPNext API credential detected in local_config.py.template"],
            find_potential_credentials(Path("local_config.py.template"), real_text),
        )


if __name__ == "__main__":
    unittest.main()
