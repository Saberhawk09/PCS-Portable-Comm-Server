import importlib.util
import io
import json
import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "pcs-admin-password-helper.py"
SPEC = importlib.util.spec_from_file_location("pcs_admin_password_helper", MODULE_PATH)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class FakeStdin:
    def __init__(self, payload: dict):
        self.buffer = io.BytesIO(json.dumps(payload).encode("utf-8"))


class PasswordHelperTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.credential = str(Path(self.tempdir.name) / "admin.json")
        self.original_credential = helper.CREDENTIAL_FILE
        helper.CREDENTIAL_FILE = self.credential
        helper.write_record(self.credential, "correct horse battery staple")

    def tearDown(self):
        helper.CREDENTIAL_FILE = self.original_credential
        self.tempdir.cleanup()

    def run_change(self, payload: dict) -> int:
        with mock.patch.object(helper.sys, "stdin", FakeStdin(payload)):
            return helper.change_from_stdin()

    def test_change_requires_current_password(self):
        code = self.run_change({
            "current_password": "incorrect current password",
            "new_password": "new correct horse battery staple",
        })
        self.assertEqual(code, 3)
        self.assertTrue(helper.verify_password("correct horse battery staple", helper.read_record(self.credential)))

    def test_change_rejects_unexpected_fields(self):
        code = self.run_change({
            "current_password": "correct horse battery staple",
            "new_password": "new correct horse battery staple",
            "unexpected": "value",
        })
        self.assertEqual(code, 2)
        self.assertTrue(helper.verify_password("correct horse battery staple", helper.read_record(self.credential)))

    def test_change_rejects_same_password(self):
        code = self.run_change({
            "current_password": "correct horse battery staple",
            "new_password": "correct horse battery staple",
        })
        self.assertEqual(code, 4)

    def test_change_rejects_oversized_password(self):
        code = self.run_change({
            "current_password": "correct horse battery staple",
            "new_password": "x" * (helper.MAXIMUM_PASSWORD_LENGTH + 1),
        })
        self.assertEqual(code, 2)

    @unittest.skipIf(os.name == "nt", "POSIX file modes are validated on Linux and the PCS")
    def test_write_record_uses_restricted_mode(self):
        self.assertEqual(oct(Path(self.credential).stat().st_mode & 0o777), "0o640")

    def test_change_replaces_hash_without_storing_plaintext(self):
        code = self.run_change({
            "current_password": "correct horse battery staple",
            "new_password": "new correct horse battery staple",
        })
        self.assertEqual(code, 0)
        record = helper.read_record(self.credential)
        self.assertFalse(helper.verify_password("correct horse battery staple", record))
        self.assertTrue(helper.verify_password("new correct horse battery staple", record))
        self.assertNotIn("new correct horse battery staple", Path(self.credential).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
