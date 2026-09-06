import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "systemd" / "pcs-buzzer.service"
spec = importlib.util.spec_from_file_location("pcs_buzzer", ROOT / "scripts" / "pcs_buzzer.py")
buzzer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = buzzer
spec.loader.exec_module(buzzer)


class FakeOutput:
    def __init__(self): self.events = []
    def tone(self, frequency, duty): self.events.append((frequency, duty))
    def off(self): self.events.append((0, 0))
    def close(self): pass


class BuzzerTests(unittest.TestCase):
    def test_service_uses_writable_lgpio_runtime_directory(self):
        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn("RuntimeDirectory=pcs-buzzer", service)
        self.assertIn("WorkingDirectory=/run/pcs-buzzer", service)
        self.assertIn("Environment=GPIOZERO_PIN_FACTORY=lgpio", service)

    def test_patterns_are_named_and_distinct(self):
        self.assertEqual(set(buzzer.PATTERNS), {"post", "ok", "warn", "bad", "low_voltage"})
        self.assertGreater(buzzer.PRIORITY["low_voltage"], buzzer.PRIORITY["bad"])
        self.assertGreater(buzzer.PRIORITY["bad"], buzzer.PRIORITY["warn"])
        self.assertEqual(buzzer.PATTERNS["post"][0].duty, 0.24)
        self.assertTrue(all(tone.duty == 0.20 for tone in buzzer.PATTERNS["ok"]))

    def test_play_always_finishes_off(self):
        output = FakeOutput()
        buzzer.play(output, "ok", sleeper=lambda _: None)
        self.assertEqual(output.events[-1], (0, 0))
        self.assertEqual([event[0] for event in output.events if event[0]], [360, 520, 760])

    def test_low_voltage_overrides_muted_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            power_path, request_path, mute_path = root / "power.json", root / "request.json", root / "muted"
            power_path.write_text(json.dumps({"low_voltage": {"active": True}}), encoding="utf-8")
            request_path.write_text(json.dumps({"pattern": "warn"}), encoding="utf-8")
            mute_path.touch()
            with mock.patch.object(buzzer, "POWER_PATH", power_path), mock.patch.object(buzzer, "REQUEST_PATH", request_path), mock.patch.object(buzzer, "MUTE_PATH", mute_path):
                self.assertEqual(buzzer.requested_pattern(), "low_voltage")


if __name__ == "__main__":
    unittest.main()
