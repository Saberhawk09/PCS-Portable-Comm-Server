import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pcs_power_stress", ROOT / "scripts" / "pcs_power_stress.py")
stress = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stress
spec.loader.exec_module(stress)


class PowerStressTests(unittest.TestCase):
    def test_dry_run_is_safe_and_describes_every_load(self):
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            self.assertEqual(0, stress.main(("--duration", "30", "--rf-seconds", "5")))
        plan = json.loads(output.getvalue().split("\nApply with", 1)[0])
        self.assertEqual("full", plan["profile"])
        self.assertEqual("full duty", plan["fan"])
        self.assertIn("255/255", plan["ws2812"])
        self.assertEqual(5, plan["sa818s_ptt_seconds"])
        self.assertEqual(11.8, plan["abort_below_input_voltage"])
        self.assertEqual(4.75, plan["abort_below_5v_voltage"])
        self.assertEqual(15, plan["maximum_input_sag_percent"])
        self.assertEqual(50, plan["power_sample_interval_ms"])
        self.assertEqual(
            ["baseline", "displays_and_fan", "cellular_upload", "cpu"],
            plan["load_sequence"],
        )
        self.assertFalse(plan["writes_performed"])

    def test_apply_and_rf_have_separate_exact_confirmations(self):
        with self.assertRaisesRegex(SystemExit, "--confirm PCS-POWER-STRESS"):
            stress.main(("--apply",))
        with self.assertRaisesRegex(SystemExit, "--confirm-rf KEY-SA818S-W8IJC-10"):
            stress.main(("--apply", "--confirm", stress.APPLY_CONFIRMATION, "--rf-seconds", "1"))

    def test_duration_and_rf_are_strictly_bounded(self):
        for arguments in (("--duration", "9"), ("--duration", "301"), ("--rf-seconds", "61")):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                stress.parse_args(arguments)

    def test_upload_url_requires_https_without_credentials(self):
        for url in ("http://example.test/up", "https://user:pass@example.test/up"):
            with self.subTest(url=url), self.assertRaises(SystemExit):
                stress.parse_args(("--upload-url", url))

    def test_source_has_cleanup_and_never_uses_a_shell_for_runtime_commands(self):
        source = (ROOT / "scripts" / "pcs_power_stress.py").read_text(encoding="utf-8")
        self.assertIn("finally:\n        stress.cleanup()", source)
        self.assertNotIn("shell=True", source)
        self.assertIn("input fell below", source)
        self.assertIn("5V rail fell below", source)
        self.assertNotIn('"cellular-connect"', source)
        self.assertIn('"--wait", "20", "connection", "up"', source)
        self.assertIn('/opt/pcs-gpio-leds/bin/python', source)
        self.assertIn('"--_ws2812-worker"', source)
        self.assertIn("FAST_INA226_CONFIG = 0x4007", source)
        self.assertIn("os.fsync(self._handle.fileno())", source)
        self.assertIn('("vcgencmd", "get_throttled")', source)
        self.assertIn('set_stage("baseline")', source)
        self.assertIn('set_stage("cellular_upload")', source)
        self.assertIn('set_stage("cpu")', source)
        self.assertIn('"--upload-file", "-"', source)
        self.assertNotIn('"--data-binary", "@-"', source)
        self.assertIn('run(("systemctl", "stop", POWER_SERVICE))', source)
        self.assertIn('run(("systemctl", "start", POWER_SERVICE)', source)

    def test_selective_profiles_only_plan_requested_loads(self):
        cpu = stress.plan(stress.parse_args(("--profile", "cpu")))
        self.assertGreater(cpu["cpu_workers"], 0)
        self.assertIsNone(cpu["cellular_upload"])
        self.assertEqual("normal service", cpu["ws2812"])
        self.assertEqual(["baseline", "cpu"], cpu["load_sequence"])

        cellular_displays = stress.plan(
            stress.parse_args(("--profile", "cellular-displays"))
        )
        self.assertEqual(0, cellular_displays["cpu_workers"])
        self.assertIsNotNone(cellular_displays["cellular_upload"])
        self.assertIn("255/255", cellular_displays["ws2812"])

        cellular_idle = stress.plan(stress.parse_args(("--profile", "cellular-idle")))
        self.assertIsNone(cellular_idle["cellular_upload"])
        self.assertEqual(["baseline", "cellular_idle"], cellular_idle["load_sequence"])

        wifi_upload = stress.plan(stress.parse_args(("--profile", "wifi-upload")))
        self.assertIsNone(wifi_upload["cellular_upload"])
        self.assertIsNotNone(wifi_upload["wifi_upload"])
        self.assertEqual(["baseline", "wifi_upload"], wifi_upload["load_sequence"])

    def test_fast_sampler_records_both_rails_and_aborts_on_low_5v(self):
        class FakeMonitor:
            def __init__(self, voltage, current, power):
                self.result = SimpleNamespace(voltage=voltage, current=current, power=power)
                self.config_writes = []

            def _write(self, register, value):
                self.config_writes.append((register, value))

            def reading(self):
                return self.result

        with tempfile.TemporaryDirectory() as directory:
            logger = stress.HighRatePowerLogger()
            logger._started_monotonic = stress.time.monotonic()
            logger.path = Path(directory) / "samples.jsonl"
            logger._handle = logger.path.open("w", encoding="utf-8", buffering=1)
            input_monitor = FakeMonitor(18.0, 1.5, 27.0)
            rail_monitor = FakeMonitor(4.74, 2.0, 9.48)
            logger._monitors = {"input": input_monitor, "rail_5v": rail_monitor}
            with mock.patch.object(logger, "_read_throttled", return_value="0x0"):
                logger._sample()
            logger._handle.close()
            logger._handle = None

            records = [json.loads(line) for line in logger.path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual({"input", "rail_5v"}, set(records[0]["rails"]))
            self.assertEqual("0x0", records[0]["pi_throttled"])
            self.assertIn("cpu_temperature_c", records[0])
            self.assertIn("5V rail fell below", logger.abort_reason)
            self.assertEqual([(0, logger.FAST_INA226_CONFIG)], input_monitor.config_writes)
            self.assertEqual([(0, logger.FAST_INA226_CONFIG)], rail_monitor.config_writes)

    def test_baseline_sag_limit_scales_for_18v_but_preserves_12v_floor(self):
        logger = stress.HighRatePowerLogger()
        logger.minimum_voltages["input"] = 17.2
        self.assertAlmostEqual(14.62, logger.arm_baseline_sag_limit(), places=2)

        logger.minimum_voltages["input"] = 12.5
        self.assertEqual(11.8, logger.arm_baseline_sag_limit())

    def test_sampler_tolerates_one_error_but_aborts_after_three_consecutive(self):
        logger = stress.HighRatePowerLogger()
        logger._sample_failed(OSError(5, "Input/output error"))
        self.assertIsNone(logger.abort_reason)
        logger._sample_failed(OSError(5, "Input/output error"))
        self.assertIsNone(logger.abort_reason)
        logger._sample_failed(OSError(5, "Input/output error"))
        self.assertIn("3 consecutive times", logger.abort_reason)


if __name__ == "__main__":
    unittest.main()
