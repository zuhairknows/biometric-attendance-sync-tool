import importlib
import unittest
from unittest import mock

from manager import paths


class ManagerEntryTests(unittest.TestCase):
    def test_importing_manager_app_as_package_succeeds(self):
        module = importlib.import_module("manager.app")

        self.assertTrue(callable(module.main))

    def test_manager_entry_calls_manager_app_main(self):
        manager_entry = importlib.import_module("manager_entry")

        with mock.patch.object(manager_entry, "main") as main:
            manager_entry.run()

        main.assert_called_once_with()

    def test_manager_spec_uses_package_entrypoint(self):
        spec_text = (paths.PROJECT_ROOT / "build" / "manager.spec").read_text(encoding="utf-8")

        self.assertIn("../manager_entry.py", spec_text)
        self.assertNotIn("../manager/app.py", spec_text)


if __name__ == "__main__":
    unittest.main()
