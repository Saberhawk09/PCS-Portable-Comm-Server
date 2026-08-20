import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = ROOT / "scripts" / "setup-pcs-base.sh"


class SetupPcsBaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SETUP_SCRIPT.read_text(encoding="utf-8")

    def test_pistar_pairing_runs_at_first_usable_lan_point(self):
        dependency_step = self.source.index(
            'run_step "Install dependencies"'
        )
        lan_step = self.source.index(
            'run_step "Configure client LAN/AP handoff on eth0"'
        )
        pairing_step = self.source.index(
            '"Pair Pi-Star coordinated shutdown"'
        )
        rtc_step = self.source.index('run_step "Configure RTC"')

        self.assertLess(dependency_step, lan_step)
        self.assertLess(lan_step, pairing_step)
        self.assertLess(pairing_step, rtc_step)

    def test_base_installer_reuses_upfront_pistar_answer(self):
        self.assertEqual(
            self.source.count('"Pair Pi-Star coordinated shutdown"'),
            1,
        )
        self.assertIn(
            'PCS_PISTAR_PAIR_CONFIRM=yes ./scripts/setup-pistar-shutdown.sh',
            self.source,
        )
        self.assertIn(
            'if [[ "${PCS_SETUP_PISTAR}" == "yes" ]]; then',
            self.source,
        )

    def test_max7219_setup_is_optional_and_reuses_configured_answer(self):
        self.assertIn('PCS_SETUP_GPIO_STATS="${PCS_SETUP_GPIO_STATS:-ask}"', self.source)
        self.assertIn('printf "PCS_SETUP_GPIO_STATS=%q\\n"', self.source)
        self.assertIn(
            'Install and start the optional MAX7219 LED matrix statistics display?',
            self.source,
        )
        self.assertIn('./scripts/setup-gpio-stats.sh --install', self.source)
        self.assertIn('PCS_SETUP_GPIO_STATS="no"', self.source)

    def test_hardware_pwm_fan_setup_is_optional_and_persisted(self):
        self.assertIn('PCS_SETUP_GPIO_FAN="${PCS_SETUP_GPIO_FAN:-ask}"', self.source)
        self.assertIn('printf "PCS_SETUP_GPIO_FAN=%q\\n"', self.source)
        self.assertIn(
            'Install GPIO18 hardware PWM thermal fan control?',
            self.source,
        )
        self.assertIn('./scripts/setup-gpio-fan.sh --install', self.source)
        self.assertIn('PCS_SETUP_GPIO_FAN="no"', self.source)


if __name__ == "__main__":
    unittest.main()
