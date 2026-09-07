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
        self.assertEqual(set(buzzer.PATTERNS), {"post", "ok", "shutdown", "warn", "bad", "low_voltage"})
        self.assertGreater(buzzer.PRIORITY["low_voltage"], buzzer.PRIORITY["bad"])
        self.assertGreater(buzzer.PRIORITY["bad"], buzzer.PRIORITY["warn"])
        self.assertEqual(buzzer.PATTERNS["post"][0].duty, 0.24)
        self.assertTrue(all(tone.duty == 0.20 for tone in buzzer.PATTERNS["ok"]))
        self.assertEqual([tone.duty for tone in buzzer.PATTERNS["warn"] if tone.frequency], [0.125, 0.125])
        self.assertEqual([tone.duty for tone in buzzer.PATTERNS["bad"] if tone.frequency], [0.425, 0.425])
        self.assertEqual([tone.duty for tone in buzzer.PATTERNS["low_voltage"] if tone.frequency], [0.50])
        self.assertEqual([tone.frequency for tone in buzzer.PATTERNS["shutdown"]], [760, 520, 360])
        self.assertGreater(buzzer.PRIORITY["shutdown"], buzzer.PRIORITY["low_voltage"])

    def test_play_always_finishes_off(self):
        output = FakeOutput()
        buzzer.play(output, "ok", sleeper=lambda _: None)
        self.assertEqual(output.events[-1], (0, 0))
        self.assertEqual([event[0] for event in output.events if event[0]], [360, 520, 760])

    def test_higher_priority_pattern_interrupts_pwm_and_settles_off(self):
        output = FakeOutput()
        sleeps = []
        checks = iter((False, True))
        buzzer.play(
            output,
            "low_voltage",
            sleeper=sleeps.append,
            interrupted=lambda: next(checks),
        )
        self.assertEqual(output.events[0], (700, 0.50))
        self.assertEqual(output.events[-1], (0, 0))
        self.assertEqual(
            sleeps,
            [buzzer.INTERRUPT_POLL_SECONDS, buzzer.PATTERN_TRANSITION_SETTLE_SECONDS],
        )

    def test_low_voltage_overrides_muted_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            power_path, request_path, mute_path = root / "power.json", root / "request.json", root / "muted"
            power_path.write_text(json.dumps({"low_voltage": {"active": True}}), encoding="utf-8")
            request_path.write_text(json.dumps({"pattern": "warn"}), encoding="utf-8")
            mute_path.touch()
            with mock.patch.object(buzzer, "POWER_PATH", power_path), mock.patch.object(buzzer, "REQUEST_PATH", request_path), mock.patch.object(buzzer, "MUTE_PATH", mute_path):
                self.assertEqual(buzzer.requested_pattern(), "low_voltage")

    def test_visual_warning_and_fault_map_to_audible_patterns(self):
        with tempfile.TemporaryDirectory() as directory:
            health_path = Path(directory) / "health.json"
            with mock.patch.object(buzzer, "HEALTH_PATH", health_path):
                health_path.write_text(json.dumps({
                    "updated_at_epoch": 100,
                    "alerts": [{"name": "gps_fix", "severity": "warning"}],
                }), encoding="utf-8")
                self.assertEqual(buzzer.raw_health_pattern(101), "warn")
                health_path.write_text(json.dumps({
                    "updated_at_epoch": 102,
                    "alerts": [{"name": "router", "severity": "critical"}],
                }), encoding="utf-8")
                self.assertEqual(buzzer.raw_health_pattern(103), "bad")
                self.assertEqual(buzzer.raw_health_pattern(200), "silent")

    def test_health_debounce_requires_a_stable_condition_and_recovery(self):
        guard = buzzer.HealthDebouncer()
        self.assertEqual(guard.update("warn", 10.0), "silent")
        self.assertEqual(guard.update("warn", 14.9), "silent")
        self.assertEqual(guard.update("warn", 15.0), "warn")
        self.assertEqual(guard.update("silent", 16.0), "warn")
        self.assertEqual(guard.update("silent", 21.0), "silent")

    def test_online_chime_plays_on_first_fresh_all_clear_health_and_only_once(self):
        guard = buzzer.OnlineChimeGuard()
        self.assertFalse(guard.should_play(health_available=False, raw_health="silent", effective_pattern="silent", now=1.0))
        self.assertFalse(guard.should_play(health_available=True, raw_health="warn", effective_pattern="warn", now=2.0))
        self.assertTrue(guard.should_play(health_available=True, raw_health="silent", effective_pattern="silent", now=10.0))
        self.assertFalse(guard.should_play(health_available=True, raw_health="silent", effective_pattern="silent", now=30.0))

    def test_health_fault_overrides_an_informational_request_and_respects_mute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path, power_path, mute_path = root / "request.json", root / "power.json", root / "muted"
            request_path.write_text(json.dumps({"pattern": "ok"}), encoding="utf-8")
            with mock.patch.object(buzzer, "REQUEST_PATH", request_path), mock.patch.object(buzzer, "POWER_PATH", power_path), mock.patch.object(buzzer, "MUTE_PATH", mute_path):
                self.assertEqual(buzzer.requested_pattern(100, "bad"), "bad")
                mute_path.touch()
                self.assertEqual(buzzer.requested_pattern(100, "bad"), "silent")

    def test_shutdown_chime_requests_highest_priority_pattern_and_waits_for_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            with mock.patch.object(buzzer, "REQUEST_PATH", request_path), mock.patch.object(buzzer.time, "time", return_value=100), mock.patch.object(buzzer.time, "sleep") as sleeper:
                self.assertEqual(buzzer.main(("shutdown-chime",)), 0)
            document = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(document, {"version": 1, "pattern": "shutdown", "expires_at_epoch": 105})
        sleeper.assert_called_once_with(buzzer.SHUTDOWN_CHIME_WAIT_SECONDS)

    def test_shutdown_chime_overrides_low_voltage_and_mute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path, power_path, mute_path = root / "request.json", root / "power.json", root / "muted"
            request_path.write_text(json.dumps({"pattern": "shutdown"}), encoding="utf-8")
            power_path.write_text(json.dumps({"low_voltage": {"active": True}}), encoding="utf-8")
            mute_path.touch()
            with mock.patch.object(buzzer, "REQUEST_PATH", request_path), mock.patch.object(buzzer, "POWER_PATH", power_path), mock.patch.object(buzzer, "MUTE_PATH", mute_path):
                self.assertEqual(buzzer.requested_pattern(100, "bad"), "shutdown")


if __name__ == "__main__":
    unittest.main()
