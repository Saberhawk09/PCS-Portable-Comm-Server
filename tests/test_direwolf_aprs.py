import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
TEMPLATE = ROOT / "config" / "direwolf.example.conf"
INSTALL_EXAMPLE = ROOT / "config" / "pcs-install.example.conf"
SETUP_SCRIPT = ROOT / "scripts" / "setup-direwolf-aprs.sh"
DOC = ROOT / "docs" / "direwolf-aprs.md"
SCRIPT_DOC = ROOT / "scripts" / "README.md"
FIREWALL_SCRIPT = ROOT / "scripts" / "pcs-aprs-kiss-firewall.sh"
FIREWALL_SERVICE = ROOT / "systemd" / "pcs-aprs-kiss-firewall.service"
DIREWOLF_OVERRIDE = ROOT / "systemd" / "pcs-direwolf-override.conf"
SOFTWARE_TEST = ROOT / "scripts" / "test-direwolf-aprs-software.sh"
SA818_UTILITY = ROOT / "scripts" / "pcs_sa818.py"
SA818_SERVICE = ROOT / "systemd" / "pcs-sa818.service"
APRS_AUDIO = ROOT / "scripts" / "pcs-aprs-audio.sh"
APRS_AUDIO_SERVICE = ROOT / "systemd" / "pcs-aprs-audio.service"


class DireWolfAprsTests(unittest.TestCase):
    def test_repository_template_is_safe_by_default(self):
        content = TEMPLATE.read_text(encoding="utf-8")
        active_lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertIn("ADEVICE plughw:CARD=Device,DEV=0 null", active_lines)
        self.assertIn("MYCALL W8IJC-10", active_lines)
        self.assertIn("GPSD localhost 2947", active_lines)
        self.assertIn("AGWPORT 0", active_lines)
        self.assertIn("KISSPORT 0", active_lines)
        for unsafe_prefix in ("PTT ", "IGLOGIN ", "IGSERVER ", "IGTXVIA ", "DIGIPEAT ", "PBEACON ", "TBEACON ", "FX25TX "):
            self.assertFalse(
                any(line.startswith(unsafe_prefix) for line in active_lines),
                f"unsafe directive is active in the default template: {unsafe_prefix}",
            )

    def test_non_secret_profile_options_are_documented(self):
        example = INSTALL_EXAMPLE.read_text(encoding="utf-8")
        documentation = DOC.read_text(encoding="utf-8")
        option_names = set(re.findall(r"^(PCS_APRS_[A-Z0-9_]+)=", example, re.MULTILINE))

        self.assertGreaterEqual(len(option_names), 18)
        for option in option_names:
            with self.subTest(option=option):
                self.assertIn(f"`{option}`", documentation)

    def test_aprs_is_passcode_has_no_install_config_key(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (INSTALL_EXAMPLE, SETUP_SCRIPT)
        )
        self.assertNotRegex(combined, r"PCS_APRS_.*PASS(?:CODE|WORD)")

    def test_selected_igate_policy_gates_all_normally_eligible_rf_packets(self):
        example = INSTALL_EXAMPLE.read_text(encoding="utf-8")
        documentation = DOC.read_text(encoding="utf-8")

        self.assertIn(
            'PCS_APRS_IGATE_RF_TO_IS_FILTER="all-eligible"',
            example,
        )
        self.assertIn("no restrictive `FILTER 0 IG` directive", documentation)

    def test_selected_igate_profile_is_normal_two_way_message_gating(self):
        example = INSTALL_EXAMPLE.read_text(encoding="utf-8")

        for setting in (
            'PCS_APRS_IGATE_MODE="two-way"',
            'PCS_APRS_IGATE_IS_TO_RF_FILTER="normal-messages"',
            'PCS_APRS_IGATE_TX_PATH="direct"',
            'PCS_APRS_IGATE_TX_LIMIT_1M="6"',
            'PCS_APRS_IGATE_TX_LIMIT_5M="10"',
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, example)

    def test_selected_easydigi_ptt_polarity_is_active_high(self):
        example = INSTALL_EXAMPLE.read_text(encoding="utf-8")
        documentation = DOC.read_text(encoding="utf-8")

        self.assertIn('PCS_APRS_PTT_GPIO_LINE="6"', example)
        self.assertIn('PCS_APRS_PTT_ACTIVE_LEVEL="high"', example)
        self.assertIn("physical header pin 31", documentation)
        self.assertIn("radio PTT to ground", documentation)

    def test_stale_gpio17_ptt_config_is_an_activation_blocker(self):
        setup = SETUP_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('[[ "${PCS_APRS_PTT_GPIO_LINE}" == "6" ]]', setup)
        self.assertIn("conflicts with the finalized GPIO6 schematic allocation", setup)

    def test_selected_tactical_frequency_is_documented(self):
        example = INSTALL_EXAMPLE.read_text(encoding="utf-8")
        documentation = DOC.read_text(encoding="utf-8")

        self.assertIn('PCS_APRS_FREQUENCY="144.550 MHz"', example)
        self.assertIn("144.550 MHz", documentation)

    def test_selected_beacon_interval_and_fill_in_policy(self):
        example = INSTALL_EXAMPLE.read_text(encoding="utf-8")
        template = TEMPLATE.read_text(encoding="utf-8")

        for setting in (
            'PCS_APRS_BEACON_INTERVAL="10:00"',
            'PCS_APRS_DIGIPEAT_ALIAS="WIDE1-1"',
            'PCS_APRS_DIGIPEAT_ALIAS_PATTERN="^WIDE1-1$"',
            'PCS_APRS_DIGIPEAT_WIDE_PATTERN="^WIDE1-1$"',
            'PCS_APRS_DIGIPEAT_PREEMPTIVE="OFF"',
            'PCS_APRS_DIGIPEAT_FILTER="all-eligible"',
            'PCS_APRS_DIGIPEAT_DEDUPE_SECONDS="30"',
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, example)

        self.assertIn("#DIGIPEAT 0 0 ^WIDE1-1$ ^WIDE1-1$", template)

    def test_tracker_uses_documented_local_pcs_gpsd_path(self):
        example = INSTALL_EXAMPLE.read_text(encoding="utf-8")
        documentation = DOC.read_text(encoding="utf-8")

        self.assertIn('PCS_APRS_GPSD="yes"', example)
        self.assertIn('PCS_APRS_GPSD_HOST="localhost"', example)
        self.assertIn('PCS_APRS_GPSD_PORT="2947"', example)
        self.assertIn("GPSD localhost 2947", documentation)
        self.assertIn("10.42.0.1:2947", documentation)

    def test_every_setup_command_flag_is_documented(self):
        setup = SETUP_SCRIPT.read_text(encoding="utf-8")
        documentation = DOC.read_text(encoding="utf-8")
        script_documentation = SCRIPT_DOC.read_text(encoding="utf-8")
        flags = {
            "--prepare", "--configure-options", "--record-validation", "--check",
            "--capabilities", "--list-audio", "--detect-audio", "--software-test", "--render-config",
            "--validate-config", "--activate-rx", "--activate-tx", "--rollback",
            "--help", "-h",
        }

        for flag in flags:
            with self.subTest(flag=flag):
                self.assertIn(flag, setup)
                self.assertIn(flag, documentation)
                self.assertIn(flag, script_documentation)

    def test_agw_and_kiss_firewall_has_lan_allow_and_catch_all_drop(self):
        script = FIREWALL_SCRIPT.read_text(encoding="utf-8")
        service = FIREWALL_SERVICE.read_text(encoding="utf-8")

        self.assertIn('ip saddr ${LAN_NETWORK} accept', script)
        self.assertIn('AGW_PORT="${PCS_APRS_AGW_PORT:-0}"', script)
        self.assertIn('PCS_APRS_FIREWALL_CONFIG:-/etc/pcs/aprs/kiss-firewall.conf', script)
        self.assertIn('port_expression="{ ${AGW_PORT}, ${KISS_PORT} }"', script)
        self.assertIn('tcp dport ${port_expression} drop', script)
        self.assertIn("Before=direwolf.service", service)
        self.assertIn("ExecStart=/usr/local/sbin/pcs-aprs-kiss-firewall --apply", service)

    def test_software_loopback_covers_ax25_fx25_and_variable_speed(self):
        script = SOFTWARE_TEST.read_text(encoding="utf-8")
        self.assertIn("gen_packets", script)
        self.assertIn("atest", script)
        self.assertIn('-X 1', script)
        self.assertIn('-v 1,1', script)
        self.assertNotRegex(script, r"\bPTT\b")

    def test_prepare_pins_supported_stable_direwolf_fallback(self):
        setup = SETUP_SCRIPT.read_text(encoding="utf-8")
        override = DIREWOLF_OVERRIDE.read_text(encoding="utf-8")

        self.assertIn('DIREWOLF_MIN_VERSION="1.8"', setup)
        self.assertIn('DIREWOLF_SOURCE_VERSION="1.8.1"', setup)
        self.assertIn(
            'DIREWOLF_SOURCE_COMMIT="a231971a652bfb574a4bae9a5d875fbce53d2267"',
            setup,
        )
        self.assertIn("libgpiod-dev libgps-dev", setup)
        self.assertIn("@DIREWOLF_BIN@", override)
        self.assertIn('sed "s|@DIREWOLF_BIN@|${direwolf_bin}|g"', setup)

    def test_production_services_order_radio_and_audio_before_direwolf(self):
        setup = SETUP_SCRIPT.read_text(encoding="utf-8")
        override = DIREWOLF_OVERRIDE.read_text(encoding="utf-8")
        radio_service = SA818_SERVICE.read_text(encoding="utf-8")
        audio_service = APRS_AUDIO_SERVICE.read_text(encoding="utf-8")
        audio_script = APRS_AUDIO.read_text(encoding="utf-8")

        self.assertIn("pcs-sa818.service", override)
        self.assertIn("pcs-aprs-audio.service", override)
        self.assertIn("ExecStartPre=/usr/local/sbin/pcs-sa818 --config /etc/pcs/aprs/sa818.ini --apply", override)
        self.assertIn("ExecStartPre=/usr/local/sbin/pcs-aprs-audio --apply --wait-seconds 60", override)
        self.assertIn("Restart=always", override)
        self.assertIn("Before=direwolf.service", radio_service)
        self.assertIn("Before=direwolf.service", audio_service)
        self.assertIn("User=direwolf", radio_service)
        self.assertIn("SupplementaryGroups=dialout", radio_service)
        self.assertIn("User=direwolf", audio_service)
        self.assertIn("SupplementaryGroups=audio", audio_service)
        self.assertIn("AT+DMOSETGROUP", SA818_UTILITY.read_text(encoding="utf-8"))
        self.assertIn('PCS_APRS_PLAYBACK_LEVEL="${PCS_APRS_PLAYBACK_LEVEL:--18dB}"', audio_script)
        self.assertIn('sset "${PCS_APRS_PLAYBACK_CONTROL}" --', audio_script)
        self.assertIn('sset "${PCS_APRS_CAPTURE_CONTROL}"', audio_script)
        self.assertIn('sset "${PCS_APRS_AGC_CONTROL}"', audio_script)
        self.assertIn('sudo systemctl restart pcs-sa818.service', setup)

    @unittest.skipIf(os.name == "nt", "Bash render execution is validated in Linux CI and on PCS")
    def test_generated_rx_profile_has_no_transmit_directives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["PCS_INSTALL_CONFIG"] = str(Path(temp_dir) / "missing.conf")
            result = subprocess.run(
                ["bash", str(SETUP_SCRIPT), "--render-config", "rx"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

        active_lines = [
            line for line in result.stdout.splitlines()
            if line and not line.startswith("#")
        ]
        self.assertTrue(any(re.match(r"^ADEVICE \S+ null$", line) for line in active_lines))
        for unsafe_prefix in ("PTT ", "IGTXVIA ", "IGTXLIMIT ", "TBEACON ", "PBEACON ", "DIGIPEAT ", "FX25TX "):
            self.assertFalse(any(line.startswith(unsafe_prefix) for line in active_lines))
        self.assertIn("IGLOGIN W8IJC-10 <APRS-IS-passcode>", active_lines)

    @unittest.skipIf(os.name == "nt", "Bash render execution is validated in Linux CI and on PCS")
    def test_generated_tx_profile_matches_commissioned_core(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["PCS_INSTALL_CONFIG"] = str(Path(temp_dir) / "missing.conf")
            result = subprocess.run(
                ["bash", str(SETUP_SCRIPT), "--render-config", "tx"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

        self.assertIn("PTT GPIOD gpiochip0 6", result.stdout)
        self.assertIn("IGTXVIA 0", result.stdout)
        self.assertIn("DIGIPEAT 0 0 ^WIDE1-1$ ^WIDE1-1$", result.stdout)
        self.assertNotIn("FX25TX", result.stdout)
        self.assertIn("TXDELAY 90", result.stdout)
        self.assertIn("TXTAIL 20", result.stdout)
        self.assertIn("AGWPORT 8000", result.stdout)
        self.assertIn('TBEACON SENDTO=IG DELAY=0:30 EVERY=10:00 SYMBOL="igate" OVERLAY=T ALT=1 COMMENT="PCS Portable Communication Server - W8IJC"', result.stdout)
        self.assertNotIn("BLOCKED", result.stdout)

    @unittest.skipIf(os.name == "nt", "Bash render execution is validated in Linux CI and on PCS")
    def test_completed_tx_profile_renders_without_blocked_directives(self):
        profile = INSTALL_EXAMPLE.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temp_dir:
            install_config = Path(temp_dir) / "pcs-install.conf"
            install_config.write_text(profile, encoding="utf-8")
            env = os.environ.copy()
            env["PCS_INSTALL_CONFIG"] = str(install_config)
            result = subprocess.run(
                ["bash", str(SETUP_SCRIPT), "--render-config", "tx"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

        self.assertNotIn("BLOCKED", result.stdout)
        self.assertIn('TBEACON SENDTO=IG DELAY=0:30 EVERY=10:00 SYMBOL="igate" OVERLAY=T ALT=1 COMMENT="PCS Portable Communication Server - W8IJC"', result.stdout)
        self.assertIn("GPSD localhost 2947", result.stdout)


if __name__ == "__main__":
    unittest.main()
