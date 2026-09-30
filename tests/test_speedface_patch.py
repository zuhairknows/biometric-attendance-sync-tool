import unittest
from scripts.apply_pyzk_speedface_patch import OLD_BLOCK, NEW_BLOCK, is_patched

class TestSpeedFacePatchDefinition(unittest.TestCase):
    def test_old_block_not_patched(self):
        self.assertFalse(is_patched(OLD_BLOCK))

    def test_new_block_patched(self):
        self.assertTrue(is_patched(NEW_BLOCK))

    def test_49_byte_advance_present(self):
        self.assertIn("attendance_data = attendance_data[49:]", NEW_BLOCK)

    def test_40_byte_fallback_present(self):
        self.assertIn("attendance_data = attendance_data[40:]", NEW_BLOCK)

if __name__ == "__main__":
    unittest.main()
