import json
import logging
import shutil
import threading
import unittest
from pathlib import Path
from unittest import mock

from config.status import JsonStatusStore, StatusStoreError


class JsonStatusStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path.cwd() / ".test-logs" / "status-store"
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
        self.temp_dir.mkdir(parents=True)
        self.path = self.temp_dir / "state" / "status.json"

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_missing_state_file_starts_empty(self):
        store = JsonStatusStore(self.path)
        self.assertIsNone(store.get("lift_off_timestamp"))

    def test_existing_state_is_loaded(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"lift_off_timestamp": "2026-09-17 10:00:00"}), encoding="utf-8")
        self.assertEqual("2026-09-17 10:00:00", JsonStatusStore(self.path).get("lift_off_timestamp"))

    def test_get_set_save_persists_state(self):
        store = JsonStatusStore(self.path)
        store.set("latest_sync_cycle", {"successful": 1}).set("device_pull_timestamp", "now")
        store.save()
        reloaded = JsonStatusStore(self.path)
        self.assertEqual({"successful": 1}, reloaded.get("latest_sync_cycle"))
        self.assertEqual("now", reloaded.get("device_pull_timestamp"))

    def test_save_uses_atomic_replacement(self):
        store = JsonStatusStore(self.path)
        store.set("mission_accomplished_timestamp", "now")
        real_replace = __import__("os").replace
        replaced = []

        def capture_replace(source, destination):
            replaced.append((Path(source), Path(destination)))
            real_replace(source, destination)

        with mock.patch("config.status.os.replace", side_effect=capture_replace):
            store.save()
        self.assertEqual(self.path, replaced[0][1])
        self.assertNotEqual(self.path, replaced[0][0])
        self.assertFalse(replaced[0][0].exists())
        self.assertEqual("now", JsonStatusStore(self.path).get("mission_accomplished_timestamp"))

    def test_works_from_background_thread(self):
        result = []

        def worker():
            store = JsonStatusStore(self.path)
            store.set("lift_off_timestamp", "background").save()
            result.append(JsonStatusStore(self.path).get("lift_off_timestamp"))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(["background"], result)

    def test_malformed_state_is_reported_and_not_replaced(self):
        self.path.parent.mkdir(parents=True)
        original = "{ malformed state"
        self.path.write_text(original, encoding="utf-8")
        logger = mock.Mock(spec=logging.Logger)
        with self.assertRaises(StatusStoreError):
            JsonStatusStore(self.path, logger=logger)
        logger.error.assert_called_once()
        self.assertEqual(original, self.path.read_text(encoding="utf-8"))

    def test_non_object_state_is_reported(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("[]", encoding="utf-8")
        with self.assertRaises(StatusStoreError):
            JsonStatusStore(self.path)


if __name__ == "__main__":
    unittest.main()
