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

    def test_wireguard_setup_is_upfront_default_off_and_all_or_nothing(self):
        self.assertIn('PCS_SETUP_WIREGUARD="${PCS_SETUP_WIREGUARD:-ask}"', self.source)
        self.assertIn('PCS_WIREGUARD_PROFILE_DEFAULT="private-config/wg-pcs.conf"', self.source)
        self.assertIn('printf "PCS_SETUP_WIREGUARD=%q\\n"', self.source)
        self.assertIn('printf "PCS_WIREGUARD_PROFILE=%q\\n"', self.source)
        self.assertIn(
            'Import and activate WireGuard remote management from ${PCS_WIREGUARD_PROFILE_DEFAULT}?',
            self.source,
        )
        self.assertIn('setup-wireguard-management.sh --validate-profile', self.source)
        self.assertIn('setup-wireguard-management.sh --prepare', self.source)
        self.assertIn('setup-wireguard-management.sh --import-profile', self.source)
        self.assertIn('setup-wireguard-management.sh --activate', self.source)
        self.assertIn('setup-wireguard-management.sh --check', self.source)
        self.assertIn('setup-wireguard-management.sh --rollback', self.source)
        self.assertIn('PCS_SETUP_WIREGUARD="no"', self.source)

    def test_wireguard_step_runs_after_network_setup_without_moving_pistar_or_rtc(self):
        dependency_step = self.source.index('run_step "Install dependencies"')
        lan_step = self.source.index('run_step "Configure client LAN/AP handoff on eth0"')
        pairing_step = self.source.index('"Pair Pi-Star coordinated shutdown"')
        rtc_step = self.source.index('run_step "Configure RTC"')
        cellular_step = self.source.index(
            'run_step "Configure cellular profile and fallback policy"'
        )
        wireguard_step = self.source.index(
            'OPTIONAL STEP: Configure WireGuard remote management'
        )

        self.assertLess(dependency_step, lan_step)
        self.assertLess(lan_step, pairing_step)
        self.assertLess(pairing_step, rtc_step)
        self.assertLess(rtc_step, cellular_step)
        self.assertLess(cellular_step, wireguard_step)

    def test_max7219_setup_is_optional_and_reuses_configured_answer(self):
        self.assertIn('PCS_SETUP_GPIO_STATS="${PCS_SETUP_GPIO_STATS:-ask}"', self.source)
        self.assertIn('printf "PCS_SETUP_GPIO_STATS=%q\\n"', self.source)
        self.assertIn(
            'Install and start the optional MAX7219 LED matrix statistics display?',
            self.source,
        )
        self.assertIn('./scripts/setup-gpio-stats.sh --install', self.source)
        self.assertIn('PCS_SETUP_GPIO_STATS="no"', self.source)

    def test_hd44780_lcd_setup_is_optional_and_persisted(self):
        self.assertIn('PCS_SETUP_GPIO_LCD="${PCS_SETUP_GPIO_LCD:-ask}"', self.source)
        self.assertIn('printf "PCS_SETUP_GPIO_LCD=%q\\n"', self.source)
        self.assertIn(
            'Install and start the optional 16x2 HD44780 LCD status display?',
            self.source,
        )
        self.assertIn('./scripts/setup-gpio-lcd.sh --install', self.source)
        self.assertIn('PCS_SETUP_GPIO_LCD="no"', self.source)

    def test_ws2812_status_setup_is_optional_and_persisted(self):
        self.assertIn('PCS_SETUP_GPIO_LEDS="${PCS_SETUP_GPIO_LEDS:-ask}"', self.source)
        self.assertIn('printf "PCS_SETUP_GPIO_LEDS=%q\\n"', self.source)
        self.assertIn(
            'Install and start the optional six-pixel WS2812 status indicators?',
            self.source,
        )
        self.assertIn('./scripts/setup-gpio-leds.sh --install', self.source)
        self.assertIn('PCS_SETUP_GPIO_LEDS="no"', self.source)

    def test_meshtastic_bluetooth_setup_is_staged_and_persisted(self):
        self.assertIn('PCS_SETUP_MESHTASTIC="${PCS_SETUP_MESHTASTIC:-ask}"', self.source)
        self.assertIn('printf "PCS_SETUP_MESHTASTIC=%q\\n"', self.source)
        self.assertIn(
            'Stage optional Meshtastic USB/Bluetooth support without connecting to or configuring a radio?',
            self.source,
        )
        self.assertIn('./scripts/setup-meshtastic-bluetooth.sh --prepare', self.source)
        self.assertIn('PCS_SETUP_MESHTASTIC="staged"', self.source)
        self.assertIn('PCS_SETUP_MESHTASTIC="no"', self.source)

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
