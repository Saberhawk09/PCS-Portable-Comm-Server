import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = ROOT / "scripts" / "setup-pcs-base.sh"
POWER_SETUP_SCRIPT = ROOT / "scripts" / "setup-power-audio.sh"
REINSTALL_STATE_SCRIPT = ROOT / "scripts" / "pcs-reinstall-state.sh"


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
            'if [[ "${PCS_SETUP_PISTAR}" == "yes" && "${PCS_PISTAR_PAIR}" != "no" ]]; then',
            self.source,
        )

    def test_wireguard_setup_is_upfront_default_off_and_all_or_nothing(self):
        self.assertIn('PCS_SETUP_WIREGUARD="${PCS_SETUP_WIREGUARD:-ask}"', self.source)
        self.assertIn('PCS_WIREGUARD_PROFILE_DEFAULT="${HOME}/wg-pcs.conf"', self.source)
        self.assertIn('printf "PCS_SETUP_WIREGUARD=%q\\n"', self.source)
        self.assertIn('printf "PCS_WIREGUARD_PROFILE=%q\\n"', self.source)
        self.assertIn(
            'Import and activate WireGuard remote management?',
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

    def test_power_monitor_and_buzzer_are_optional_and_persisted(self):
        for name in ("PCS_SETUP_POWER_MONITOR", "PCS_POWER_PROFILE", "PCS_SETUP_BUZZER"):
            self.assertIn(f'{name}="${{{name}:-ask}}"', self.source)
            self.assertIn(f'printf "{name}=%q\\n"', self.source)
        for name in ("PCS_SETUP_POWER_MONITOR", "PCS_SETUP_BUZZER"):
            self.assertIn(f'{name}="no"', self.source)
        self.assertIn('PCS_POWER_PROFILE="generic"', self.source)
        self.assertIn('INA226 configuration profile', self.source)
        self.assertIn('generic commissioned-pcs', self.source)
        self.assertIn('./scripts/setup-power-audio.sh --install-power', self.source)
        self.assertIn('./scripts/setup-power-audio.sh --install-buzzer', self.source)
        buzzer = self.source.index('./scripts/setup-power-audio.sh --install-buzzer')
        control_panel = self.source.index('run_step "Install PCS Control Panel"')
        final_status = self.source.index('STEP: Final PCS status')
        self.assertLess(control_panel, buzzer)
        self.assertLess(buzzer, final_status)

    def test_power_monitor_can_finish_across_required_i2c_reboot(self):
        power = POWER_SETUP_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('PCS_ALLOW_REBOOT_DEFER:-no', power)
        self.assertIn('systemctl enable pcs-power-monitor.service', power)
        self.assertIn('run ./scripts/pcs-self-test.sh to complete hardware validation', self.source)

    def test_installer_does_not_change_tracked_source_modes(self):
        self.assertNotIn('chmod +x "${script}"', self.source)
        self.assertNotIn('chmod +x web/pcs-control-panel', self.source)

    def test_exact_recovery_preserves_admin_credential_without_prompt(self):
        control = (ROOT / "scripts" / "setup-pcs-control-panel.sh").read_text(encoding="utf-8")
        self.assertIn('PCS_PRESERVE_ADMIN_PASSWORD:-no', control)
        self.assertIn('PCS_PRESERVE_ADMIN_PASSWORD=yes ./scripts/setup-pcs-control-panel.sh', self.source)

    def test_aprs_identity_and_passcode_are_collected_without_persisting_secret(self):
        self.assertIn('ask_value "APRS base callsign"', self.source)
        self.assertIn('ask_choice "APRS SSID"', self.source)
        self.assertIn('ask_secret_confirm "APRS-IS passcode for ${PCS_APRS_CALLSIGN}"', self.source)
        self.assertNotIn('printf "PCS_APRS_IS_PASSCODE=%q', self.source)
        self.assertNotIn('printf "PCS_APRS_CALLSIGN_BASE=%q', self.source)
        self.assertNotIn('printf "PCS_APRS_SSID=%q', self.source)

    def test_lite_preflight_enforces_fixed_runtime_identity_and_path(self):
        preflight_call = self.source.index("validate_host_preflight\n")
        setup_choice = self.source.index("choose_setup_mode", preflight_call)
        self.assertLess(preflight_call, setup_choice)
        self.assertIn('current_user}" != "pi"', self.source)
        self.assertIn('/home/pi/Projects/PCS-Portable-Comm-Server', self.source)
        self.assertIn('A graphical desktop is not required', self.source)
        self.assertIn('for command in apt-get systemctl python3', self.source)

    def test_file_share_discovery_is_a_repeatable_base_step(self):
        backup = self.source.index('run_step "Configure automatic PCS backups"')
        discovery = self.source.index('run_step "Configure LAN file-share discovery"')
        control = self.source.index('run_step "Install PCS Control Panel"')
        self.assertLess(backup, discovery)
        self.assertLess(discovery, control)
        self.assertIn('ensure_executable "scripts/setup-pcs-share-discovery.sh"', self.source)

    def test_one_command_install_cannot_hide_selected_step_or_self_test_failure(self):
        self.assertIn('OPTIONAL_STEP_FAILURES=$((OPTIONAL_STEP_FAILURES + 1))', self.source)
        self.assertIn('FINAL_SELF_TEST_PASSED=1', self.source)
        self.assertIn('PCS base setup is incomplete', self.source)
        self.assertIn('PCS one-command installation completed successfully.', self.source)
        failure_gate = self.source.rindex(
            'if (( OPTIONAL_STEP_FAILURES > 0 || FINAL_SELF_TEST_PASSED == 0 ))'
        )
        success = self.source.rindex('PCS one-command installation completed successfully.')
        self.assertLess(failure_gate, success)

    def test_commissioned_rebuild_mode_restores_local_hardware_without_secrets(self):
        self.assertIn('COMMISSIONED - Rebuild this PCS hardware profile', self.source)
        self.assertIn('PCS_SETUP_MODE="COMMISSIONED"', self.source)
        commissioned = self.source.index('        COMMISSIONED)')
        defaults = self.source.index('        DEFAULTS)', commissioned)
        block = self.source[commissioned:defaults]
        for expected in (
            'PCS_CELLULAR_FALLBACK_MODE="wifi-fallback"',
            'PCS_UPLINK_MODE="auto"',
            'PCS_SETUP_WWAN_GPS="yes"',
            'PCS_SETUP_GPSD_LAN="yes"',
            'PCS_SETUP_APRS="staged"',
            'PCS_SETUP_MESHTASTIC="staged"',
            'PCS_POWER_PROFILE="commissioned-pcs"',
            'PCS_SETUP_STARLINK_TELEMETRY="yes"',
            'PCS_STARLINK_AUTODETECT="yes"',
        ):
            self.assertIn(expected, block)
        self.assertIn('PCS_SETUP_WIREGUARD="no"', block)
        self.assertIn('PCS_SETUP_PISTAR="yes"', block)
        self.assertIn('PCS_PISTAR_PAIR="no"', block)
        self.assertNotIn('PCS_APRS_IS_PASSCODE=', block)

    def test_starlink_telemetry_is_an_explicit_repeatable_installer_choice(self):
        self.assertIn('PCS_SETUP_STARLINK_TELEMETRY="${PCS_SETUP_STARLINK_TELEMETRY:-no}"', self.source)
        self.assertIn('printf "PCS_SETUP_STARLINK_TELEMETRY=%q\\n"', self.source)
        self.assertIn('setup-starlink-telemetry.sh --install', self.source)

    def test_commissioned_uplink_detection_is_fail_closed(self):
        source = (ROOT / "scripts" / "pcs_uplink_setup.py").read_text(encoding="utf-8")
        self.assertIn("env.get('PCS_STARLINK_AUTODETECT'", source)
        self.assertIn("multiple Ethernet WAN candidates found", source)
        self.assertIn("no non-LAN Ethernet WAN candidate found", source)


class PowerSetupTests(unittest.TestCase):
    def test_power_install_enables_bounded_persistent_diagnostics(self):
        source = POWER_SETUP_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('config/pcs-journald-persistent.conf', source)
        self.assertIn('/etc/systemd/journald.conf.d/90-pcs-persistent-diagnostics.conf', source)
        self.assertIn('sudo journalctl --flush', source)

    def test_commissioned_profile_is_explicit_and_validated_before_install(self):
        source = POWER_SETUP_SCRIPT.read_text(encoding="utf-8")
        validation = source.index('config/power-monitor.pcs.json')
        install = source.index('sudo install -o root -g root -m 0644', validation)
        self.assertLess(validation, install)
        self.assertIn('commissioned-pcs)', source)
        self.assertIn('PCS_POWER_PROFILE must be generic or commissioned-pcs', source)


class ReinstallStateTests(unittest.TestCase):
    def test_export_is_private_bounded_and_never_overwrites(self):
        source = REINSTALL_STATE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('Refusing to place credential-bearing state inside the repository', source)
        self.assertIn('Refusing to overwrite', source)
        self.assertIn('Refusing to save recovery state on the SD-card root filesystem', source)
        self.assertIn('openssl enc -aes-256-cbc -pbkdf2 -salt', source)
        self.assertIn('Passphrase must contain at least 12 characters', source)
        self.assertIn('removed the incomplete archive', source)
        self.assertIn('Encrypted archive verification failed', source)
        self.assertIn('tar -tzf - >/dev/null', source)
        self.assertIn('find -P "${path}" -xdev', source)
        self.assertIn('--no-recursion', source)
        self.assertIn('sudo chmod 0600 "${archive}"', source)
        self.assertIn('rm -f -- "${list_file:-}"', source)
        self.assertIn('digest="$(sudo sha256sum "${archive}"', source)
        self.assertIn('etc/pcs', source)
        self.assertIn('etc/wireguard', source)
        self.assertIn('etc/ssh', source)
        self.assertIn('home/pi/.ssh', source)
        self.assertIn('etc/NetworkManager/system-connections', source)
        self.assertIn('var/lib/samba/private', source)
        self.assertIn('var/lib/bluetooth', source)
        self.assertIn('etc/shadow', source)
        self.assertIn('pi account password hash', source)


if __name__ == "__main__":
    unittest.main()
