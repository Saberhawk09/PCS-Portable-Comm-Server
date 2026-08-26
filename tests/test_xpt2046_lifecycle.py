import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test-xpt2046-display.sh"


class Xpt2046LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = SCRIPT.read_text(encoding="utf-8")

    def test_all_overlapping_gpio_services_are_managed(self):
        for service in (
            "pcs-gpio-startup.service",
            "pcs-gpio-lcd.service",
            "pcs-gpio-leds.service",
            "pcs-gpio-stats.service",
            "pcs-gpio-fan.service",
            "pcs-gpio-shutdown.service",
        ):
            with self.subTest(service=service):
                self.assertIn(service, self.script)

    def test_startup_and_led_units_cannot_pull_gpio_back_in_during_test(self):
        stop_order = self.script.split("STOP_ORDER=(", 1)[1].split(")", 1)[0]
        for service in ("pcs-gpio-startup.service", "pcs-gpio-leds.service"):
            with self.subTest(service=service):
                self.assertIn(service, stop_order)

    def test_mutating_actions_have_explicit_apply_and_hardware_gates(self):
        self.assertIn("prepare requires --apply", self.script)
        self.assertIn("--confirm-test-screen-disconnected", self.script)
        self.assertIn("test requires --apply", self.script)
        self.assertIn("--confirm-pcs-displays-disconnected", self.script)
        self.assertIn("restore requires --apply", self.script)
        self.assertIn("--confirm-pcs-hardware-restored", self.script)

    def test_display_overlay_is_dynamic_and_has_cleanup_trap(self):
        self.assertIn("dtoverlay fbtft spi0-0 ili9486", self.script)
        self.assertIn("trap on_exit EXIT", self.script)
        self.assertIn("trap 'exit 130' INT", self.script)
        self.assertIn("dtoverlay -r fbtft", self.script)
        self.assertNotIn("/boot/config.txt", self.script)

    def test_touch_overlay_is_dynamic_and_removed_before_display(self):
        self.assertIn("dtoverlay ads7846 cs=1", self.script)
        self.assertIn("penirq=17", self.script)
        self.assertIn("dtoverlay -r ads7846", self.script)
        self.assertLess(
            self.script.index("dtoverlay -r ads7846"),
            self.script.index("dtoverlay -r fbtft", self.script.index("run_touch_test")),
        )

    def test_no_background_service_is_installed(self):
        self.assertNotIn("daemon-reload", self.script)
        self.assertNotIn("systemd/system", self.script)
        self.assertNotIn("crontab", self.script)


if __name__ == "__main__":
    unittest.main()
