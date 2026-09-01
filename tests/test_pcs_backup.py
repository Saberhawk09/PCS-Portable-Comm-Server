import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


config = load_module("pcs_backup_config", ROOT / "scripts" / "pcs_backup_config.py")
runner = load_module("pcs_auto_backup", ROOT / "scripts" / "pcs_auto_backup.py")


class BackupConfigTests(unittest.TestCase):
    def test_policy_validation_is_exact_and_bounded(self):
        self.assertEqual(
            config.validate_config({"enabled": True, "interval_minutes": 10, "keep_history": False}, require_version=False),
            {"version": 2, "enabled": True, "interval_minutes": 10, "keep_history": False},
        )
        for value in (
            {"enabled": 1, "interval_minutes": 10, "keep_history": False},
            {"enabled": True, "interval_minutes": True, "keep_history": False},
            {"enabled": True, "interval_minutes": 0, "keep_history": False},
            {"enabled": True, "interval_minutes": 43_201, "keep_history": False},
            {"enabled": True, "interval_minutes": 10, "keep_history": 1},
            {"enabled": True, "interval_minutes": 10, "keep_history": False, "extra": "blocked"},
        ):
            with self.subTest(value=value), self.assertRaises(config.ConfigError):
                config.validate_config(value, require_version=False)

    def test_config_write_is_atomic_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config.write_config({"version": 2, "enabled": False, "interval_minutes": 8, "keep_history": True}, path)
            self.assertEqual(config.load_config(path)["interval_minutes"], 8)
            self.assertFalse(config.load_config(path)["enabled"])
            self.assertTrue(config.load_config(path)["keep_history"])
            self.assertEqual(list(path.parent.glob(".config.*.tmp")), [])

    def test_api_facing_update_uses_only_validated_host_service_arguments(self):
        completed = subprocess.CompletedProcess(
            [], 0, '{"version":2,"enabled":true,"interval_minutes":6,"keep_history":true}\n', "",
        )
        with mock.patch.object(config.subprocess, "run", return_value=completed) as process, \
             mock.patch.object(config.sys, "argv", ["/usr/local/sbin/pcs-backup-config"]):
            value = config.update_on_host({"enabled": True, "interval_minutes": 6, "keep_history": True})
        self.assertEqual(value["interval_minutes"], 6)
        command = process.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/systemd-run")
        self.assertIn("PCS_BACKUP_HOST_ACTION=1", command)
        self.assertEqual(command[-4:], ["apply-host", "true", "6", "true"])

    def test_v1_hour_config_is_migrated_without_enabling_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"version":1,"enabled":false,"interval_hours":4}', encoding="utf-8")
            self.assertEqual(
                config.load_config(path),
                {"version": 2, "enabled": False, "interval_minutes": 240, "keep_history": False},
            )

    def test_failed_runtime_change_rolls_back_previous_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            previous_path = config.CONFIG_FILE
            config.CONFIG_FILE = path
            config.write_config({"version": 2, "enabled": True, "interval_minutes": 10, "keep_history": False}, path)
            try:
                with mock.patch.object(config, "apply_runtime", side_effect=[RuntimeError("failed"), None]):
                    with self.assertRaises(RuntimeError):
                        config.update_config({"enabled": False, "interval_minutes": 4, "keep_history": True})
                self.assertEqual(config.load_config(path), {"version": 2, "enabled": True, "interval_minutes": 10, "keep_history": False})
            finally:
                config.CONFIG_FILE = previous_path


class AutomaticBackupRunnerTests(unittest.TestCase):
    def test_missing_timestamp_is_due_and_recent_timestamp_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "LAST_SYNC.txt"
            previous = runner.LAST_SYNC_FILE
            runner.LAST_SYNC_FILE = path
            try:
                self.assertTrue(runner.backup_due(10))
                path.write_text("now\n", encoding="utf-8")
                os.utime(path, (time.time(), time.time()))
                self.assertFalse(runner.backup_due(10))
                os.utime(path, (time.time() - 11 * 60, time.time() - 11 * 60))
                self.assertTrue(runner.backup_due(10))
            finally:
                runner.LAST_SYNC_FILE = previous

    def test_timer_units_are_fixed_and_base_installer_includes_setup(self):
        timer = (ROOT / "systemd" / "pcs-backup.timer").read_text(encoding="utf-8")
        service = (ROOT / "systemd" / "pcs-backup.service").read_text(encoding="utf-8")
        base = (ROOT / "scripts" / "setup-pcs-base.sh").read_text(encoding="utf-8")
        dispatcher = (ROOT / "scripts" / "pcs-web-action.sh").read_text(encoding="utf-8")
        self.assertIn("OnUnitActiveSec=1min", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("ExecStart=/usr/local/sbin/pcs-auto-backup", service)
        self.assertIn('run_step "Configure automatic PCS backups"', base)
        self.assertIn("flock -n", dispatcher)
        self.assertNotIn("rsync -rtvh --delete", dispatcher)
        self.assertIn("PCS-Backup-History", dispatcher)
        standalone = (ROOT / "scripts" / "sync-pcs-share-to-backup.sh").read_text(encoding="utf-8")
        self.assertNotIn("--delete", standalone)

    def test_concurrent_manual_backup_is_a_successful_timer_skip(self):
        with mock.patch.object(runner, "load_config", return_value={"enabled": True, "interval_minutes": 10, "keep_history": False}), \
             mock.patch.object(runner, "backup_due", return_value=True), \
             mock.patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 75)):
            self.assertEqual(runner.main(), 0)


if __name__ == "__main__":
    unittest.main()
