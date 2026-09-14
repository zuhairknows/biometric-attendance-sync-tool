import shutil
import os
import unittest
from pathlib import Path

from config.secrets import (
    InvalidSecretId,
    SecretNotFound,
    SecretProtectionError,
    SecretStore,
    WindowsMachineDPAPIProtector,
    delete_secret,
    device_password_secret_id,
    get_secret,
    secret_exists,
    set_secret,
)
from tests.test_config_loader import FakePaths, FakeProtector


class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path.cwd() / ".test-logs" / self._testMethodName
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True)
        self.paths = FakePaths(self.test_dir)
        self.store = SecretStore(self.paths.get_secrets_dir(), protector=FakeProtector())

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_secret_store_crud(self):
        self.assertFalse(self.store.secret_exists("erpnext/api_key"))

        self.store.set_secret("erpnext/api_key", "key")

        self.assertTrue(self.store.secret_exists("erpnext/api_key"))
        self.assertEqual(self.store.get_secret("erpnext/api_key"), "key")
        self.assertTrue(self.store.delete_secret("erpnext/api_key"))
        self.assertFalse(self.store.secret_exists("erpnext/api_key"))

    def test_module_level_secret_api_uses_paths_module(self):
        set_secret("erpnext/api_secret", "secret", paths_module=self.paths, protector=FakeProtector())

        self.assertTrue(secret_exists("erpnext/api_secret", paths_module=self.paths, protector=FakeProtector()))
        self.assertEqual(get_secret("erpnext/api_secret", paths_module=self.paths, protector=FakeProtector()), "secret")
        self.assertTrue(delete_secret("erpnext/api_secret", paths_module=self.paths, protector=FakeProtector()))

    def test_secret_file_does_not_contain_plaintext(self):
        self.store.set_secret("erpnext/api_secret", "SUPER_SECRET_VALUE")

        secret_file = self.paths.get_secrets_dir() / "erpnext" / "api_secret.secret"
        contents = secret_file.read_bytes()

        self.assertNotIn(b"SUPER_SECRET_VALUE", contents)

    def test_missing_secret_raises(self):
        with self.assertRaises(SecretNotFound):
            self.store.get_secret("erpnext/api_secret")

    def test_corrupt_secret_file_raises(self):
        secret_file = self.paths.get_secrets_dir() / "erpnext" / "api_secret.secret"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_bytes(b"not-a-protected-secret")

        with self.assertRaises(SecretProtectionError):
            self.store.get_secret("erpnext/api_secret")

    def test_decryption_failure_raises(self):
        secret_file = self.paths.get_secrets_dir() / "erpnext" / "api_secret.secret"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_bytes(b"BAS1\nbad-ciphertext")

        with self.assertRaises(SecretProtectionError):
            self.store.get_secret("erpnext/api_secret")

    def test_secret_ids_reject_path_traversal(self):
        for secret_id in ["../secret", "erpnext/../secret", "C:/secret", "erpnext\\secret", ""]:
            with self.subTest(secret_id=secret_id):
                with self.assertRaises(InvalidSecretId):
                    self.store.get_secret(secret_id)

    def test_device_password_secret_id_validates_device_id(self):
        self.assertEqual(device_password_secret_id("DEVICE_01"), "devices/DEVICE_01/password")
        with self.assertRaises(ValueError):
            device_password_secret_id("../DEVICE_01")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is available only on Windows.")
    def test_windows_machine_dpapi_round_trip(self):
        store = SecretStore(self.paths.get_secrets_dir(), protector=WindowsMachineDPAPIProtector())

        store.set_secret("erpnext/api_key", "windows-key")

        self.assertEqual(store.get_secret("erpnext/api_key"), "windows-key")


if __name__ == "__main__":
    unittest.main()
