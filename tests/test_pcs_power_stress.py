import importlib.util
import io
import json
from pathlib import Path
import sys
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
        self.assertEqual("full duty", plan["fan"])
        self.assertIn("255/255", plan["ws2812"])
        self.assertEqual(5, plan["sa818s_ptt_seconds"])
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
        self.assertIn('"low input voltage detected; ending stress immediately"', source)
        self.assertIn('/opt/pcs-gpio-leds/bin/python', source)
        self.assertIn('"--_ws2812-worker"', source)


if __name__ == "__main__":
    unittest.main()
