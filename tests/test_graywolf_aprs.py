import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL_EXAMPLE = ROOT / "config" / "pcs-install.example.conf"
BASE_SETUP = ROOT / "scripts" / "setup-pcs-base.sh"
GRAYWOLF_SETUP = ROOT / "scripts" / "setup-graywolf-aprs.sh"
GRAYWOLF_PROFILE = ROOT / "scripts" / "pcs-graywolf-profile.py"
GRAYWOLF_OVERRIDE = ROOT / "systemd" / "pcs-graywolf-override.conf"
DIREWOLF_OVERRIDE = ROOT / "systemd" / "pcs-direwolf-override.conf"
PTT_SAFE_SCRIPT = ROOT / "scripts" / "pcs-aprs-ptt-safe.sh"
PTT_SAFE_SERVICE = ROOT / "systemd" / "pcs-aprs-ptt-safe.service"
SELF_TEST = ROOT / "scripts" / "pcs-self-test.sh"
STATUS = ROOT / "scripts" / "pcs-status.sh"
WEB_ACTION = ROOT / "scripts" / "pcs-web-action.sh"
DOC = ROOT / "docs" / "graywolf-aprs.md"


class GraywolfAprsTests(unittest.TestCase):
    def test_default_engine_remains_commissioned_direwolf(self):
        example = INSTALL_EXAMPLE.read_text(encoding="utf-8")

        self.assertIn('PCS_APRS_ENGINE="direwolf"', example)
        self.assertIn('PCS_GRAYWOLF_HTTP_ADDRESS="10.42.0.1"', example)
        self.assertIn('PCS_GRAYWOLF_HTTP_PORT="8070"', example)

    def test_base_installer_offers_only_known_engines_and_dispatches_selected_one(self):
        setup = BASE_SETUP.read_text(encoding="utf-8")

        self.assertIn('ask_choice "APRS software engine" "${PCS_APRS_ENGINE}" direwolf graywolf', setup)
        self.assertIn("validate_aprs_engine()", setup)
        self.assertIn("direwolf|graywolf)", setup)
        self.assertIn('aprs_setup_script="./scripts/setup-${PCS_APRS_ENGINE}-aprs.sh"', setup)
        self.assertIn('if "${aprs_setup_script}" --prepare; then', setup)
        self.assertIn('printf "PCS_APRS_ENGINE=%q\\n"', setup)

    def test_release_packages_and_hashes_are_pinned_per_supported_architecture(self):
        setup = GRAYWOLF_SETUP.read_text(encoding="utf-8")

        self.assertIn('GRAYWOLF_VERSION="0.14.13"', setup)
        expected = {
            "amd64": "660e79f0d0779575fb049506dc05a1d96799c697a0c94598cbf7eaf64dd98360",
            "arm64": "1c85cb35e8ffbf364aa7fccc4b62f2c344e847739f4323ba5fac663de7167ed7",
            "armhf": "4885291f0138c9417ffef415d0f473c451d68742ccdd4f9cb3951dcc5fd933fa",
        }
        for architecture, digest in expected.items():
            with self.subTest(architecture=architecture):
                self.assertIn(f"graywolf_${{GRAYWOLF_VERSION}}_{architecture}.deb", setup)
                self.assertIn(digest, setup)
        self.assertIn("sha256sum --check --status", setup)
        self.assertIn("--proto '=https' --tlsv1.2", setup)

    def test_prepare_preserves_direwolf_and_leaves_graywolf_inactive(self):
        setup = GRAYWOLF_SETUP.read_text(encoding="utf-8")
        graywolf_guard = setup.index("systemctl is-active --quiet graywolf.service")
        install = setup.index('sudo apt-get install -y gpiod "${temp_dir}/${package_name}"')
        disable = setup.index("systemctl disable --now graywolf.service")

        self.assertLess(graywolf_guard, install)
        self.assertLess(install, disable)
        self.assertIn('if ! systemctl is-active --quiet direwolf.service', setup)
        self.assertNotRegex(setup, r"systemctl\s+(?:enable|start|restart)\s+graywolf[.]service")

    def test_prepare_stages_without_removing_direwolf_or_creating_rf_profile(self):
        setup = GRAYWOLF_SETUP.read_text(encoding="utf-8")

        self.assertIn('set_install_config_value "PCS_APRS_ENGINE_STAGED" "graywolf"', setup)
        self.assertIn('set_install_config_value "PCS_APRS_ENGINE" "graywolf"', setup)
        self.assertIn('set_install_config_value "PCS_SETUP_APRS" "staged"', setup)
        self.assertIn('STAGED_MARKER="${APRS_CONFIG_DIR}/graywolf-staged"', setup)
        self.assertNotRegex(setup, r"apt(?:-get)?\s+(?:purge|remove).*direwolf")
        self.assertNotIn("/etc/direwolf.conf", setup)
        self.assertNotIn("--activate-rx", setup)
        self.assertNotIn("--activate-tx", setup)

    def test_http_endpoint_refuses_pcs_reserved_port(self):
        setup = GRAYWOLF_SETUP.read_text(encoding="utf-8")
        override = GRAYWOLF_OVERRIDE.read_text(encoding="utf-8")

        self.assertIn("PCS_GRAYWOLF_HTTP_PORT == 8080", setup)
        self.assertIn('PCS_APRS_GRAYWOLF_HTTP_PORT', setup)
        self.assertIn("tcp/8080 is reserved by the PCS legacy admin redirect", setup)
        self.assertIn("@GRAYWOLF_HTTP_LISTEN@", override)
        self.assertIn("-http @GRAYWOLF_HTTP_LISTEN@", override)
        self.assertIn("-history-db /run/graywolf/history.db", override)

    def test_systemd_enforces_mutual_exclusion_in_both_directions(self):
        graywolf = GRAYWOLF_OVERRIDE.read_text(encoding="utf-8")
        direwolf = DIREWOLF_OVERRIDE.read_text(encoding="utf-8")

        self.assertIn("Conflicts=direwolf.service", graywolf)
        self.assertIn("Conflicts=graywolf.service", direwolf)

    def test_graywolf_staging_installs_the_shared_ptt_guard(self):
        setup = GRAYWOLF_SETUP.read_text(encoding="utf-8")
        graywolf = GRAYWOLF_OVERRIDE.read_text(encoding="utf-8")
        guard = PTT_SAFE_SERVICE.read_text(encoding="utf-8")
        helper = PTT_SAFE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('sudo apt-get install -y gpiod', setup)
        self.assertIn('PTT_SAFE_SRC=', setup)
        self.assertIn('PCS_APRS_PTT_LINE=%q', setup)
        self.assertIn('enable --now pcs-aprs-ptt-safe.service', setup)
        self.assertIn('pcs-aprs-ptt-safe.service', graywolf)
        self.assertIn('ExecStopPost=+/usr/bin/systemctl --no-block start pcs-aprs-ptt-safe.service', graywolf)
        self.assertIn('After=direwolf.service graywolf.service', guard)
        self.assertIn('"${PTT_LINE}=0"', helper)

    def test_profile_provisioner_keeps_transmitters_disabled_and_repairs_defaults(self):
        profile = GRAYWOLF_PROFILE.read_text(encoding="utf-8")

        self.assertIn('"output_device_id": output_id', profile)
        self.assertIn('"method": "gpio"', profile)
        self.assertIn('"gpio_line": args.gpio_line', profile)
        self.assertIn('"simulation_mode": True', profile)
        self.assertGreaterEqual(profile.count('"enabled": False'), 6)
        self.assertIn("digipeater/rules/{int(rule['id'])}", profile)
        self.assertIn("beacons/{int(beacon['id'])}", profile)
        self.assertIn('beacons[0].get("path") != ""', profile)

    def test_shared_aprs_prerequisites_order_before_either_engine(self):
        for relative_path in (
            "systemd/pcs-sa818.service",
            "systemd/pcs-aprs-audio.service",
            "systemd/pcs-aprs-kiss-firewall.service",
        ):
            with self.subTest(path=relative_path):
                content = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("Before=direwolf.service graywolf.service", content)

    def test_status_self_test_and_dashboard_read_selected_engine(self):
        for path in (STATUS, SELF_TEST, WEB_ACTION):
            with self.subTest(path=path.name):
                content = path.read_text(encoding="utf-8")
                self.assertIn("PCS_APRS_ENGINE", content)
                self.assertIn("graywolf.service", content)
                self.assertIn("direwolf.service", content)

    def test_documentation_preserves_activation_and_migration_gates(self):
        documentation = DOC.read_text(encoding="utf-8")

        self.assertIn("does not yet provide a supported Graywolf", documentation)
        self.assertIn("Existing Dire Wolf configuration is not removed", documentation)
        self.assertIn("Do not enable Graywolf manually", documentation)
        self.assertIn("dependency audit and refactor", documentation)
        self.assertIn("supervised RX and TX acceptance", documentation)


if __name__ == "__main__":
    unittest.main()
