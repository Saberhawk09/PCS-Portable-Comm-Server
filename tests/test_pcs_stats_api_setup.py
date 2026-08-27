import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "setup-pcs-stats-api.sh"
FIREWALL = ROOT / "scripts" / "pcs-stats-api-firewall.sh"
API = ROOT / "web" / "pcs-control-panel" / "pcs_stats_api.py"
TOKEN_HELPER = ROOT / "scripts" / "pcs_api_token.py"
POLICY = ROOT / "config" / "pcs-stats-api.example.conf"
SERVICE = ROOT / "systemd" / "pcs-stats-api.service"
FIREWALL_SERVICE = ROOT / "systemd" / "pcs-stats-api-firewall.service"
DOC = ROOT / "docs" / "pcs-stats-api.md"
SCRIPT_DOC = ROOT / "scripts" / "README.md"
BASE_INSTALLER = ROOT / "scripts" / "setup-pcs-base.sh"


class StatsApiSetupTests(unittest.TestCase):
    def test_runtime_is_separate_default_disabled_and_not_in_base_installer(self):
        source = API.read_text(encoding="utf-8")
        setup = SETUP.read_text(encoding="utf-8")
        base = BASE_INSTALLER.read_text(encoding="utf-8")
        self.assertIn('API_ENABLED = os.environ.get("PCS_API_ENABLED", "no")', source)
        self.assertIn("disable --now pcs-stats-api.service pcs-stats-api-firewall.service", setup)
        self.assertNotIn("setup-pcs-stats-api", base)

    def test_service_is_dedicated_tls_only_and_firewall_ordered(self):
        service = SERVICE.read_text(encoding="utf-8")
        firewall_service = FIREWALL_SERVICE.read_text(encoding="utf-8")
        self.assertIn("User=pcs-api", service)
        self.assertIn("Group=pcs-api", service)
        self.assertIn("Requires=pcs-stats-api-firewall.service", service)
        self.assertIn("--cert-file ${PCS_API_CERT_FILE}", service)
        self.assertIn("--key-file ${PCS_API_KEY_FILE}", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn(
            "ReadWritePaths=/etc/pcs-stats-api /etc/pcs-control-panel",
            service,
        )
        self.assertNotIn("ReadWritePaths=/etc\n", service)
        self.assertIn("Before=pcs-stats-api.service", firewall_service)
        self.assertIn("RemainAfterExit=yes", firewall_service)

    def test_sudoers_scope_contains_only_collectors_pairing_and_fixed_panel_actions(self):
        setup = SETUP.read_text(encoding="utf-8")
        marker = "# PCS Stats API may invoke fixed collectors, fixed pairing, and only the web panel's exact administrative actions."
        start = setup.index(marker)
        sudoers_block = setup[start:setup.index("EOF", start)]
        self.assertIn("dashboard-public-json", sudoers_block)
        self.assertIn("dashboard-json", sudoers_block)
        self.assertIn("/usr/local/sbin/pcs-api-token --file /etc/pcs-stats-api/api-read-tokens.json pair-from-stdin", sudoers_block)
        self.assertIn("/usr/local/sbin/pcs-admin-password-helper --change-from-stdin", sudoers_block)
        self.assertNotIn(" issue ", sudoers_block)
        self.assertNotIn(" revoke ", sudoers_block)
        self.assertNotIn(" list", sudoers_block)
        expected_actions = {
            "status", "self-test", "storage-status", "restart-logs", "wifi-status",
            "wifi-connect", "wifi-disconnect", "cellular-status", "cellular-connect",
            "cellular-disconnect", "cellular-test", "meshtastic-status",
            "restart-meshtastic", "sync-backup", "mount-usb", "mount-new-usb",
            "safe-unmount-usb", "restart-services", "restart-samba",
            "restart-modemmanager", "sync-time", "restart-chrony", "restart-gpsd",
            "reboot-system", "shutdown-system",
        }
        for action in expected_actions:
            self.assertIn(f"/usr/local/sbin/pcs-web-action {action}", sudoers_block)
        self.assertNotIn("arbitrary", sudoers_block)
        self.assertNotIn(" ALL\n", sudoers_block)

    def test_runtime_names_the_fixed_pairing_helper_and_check_validates_sudoers(self):
        setup = SETUP.read_text(encoding="utf-8")
        self.assertIn("PCS_API_TOKEN_HELPER=${TOKEN_HELPER_DST}", setup)
        self.assertIn("PCS_ADMIN_PASSWORD_HELPER=${PASSWORD_HELPER_DST}", setup)
        self.assertIn('PASSWORD_HELPER_DST="/usr/local/sbin/pcs-admin-password-helper"', setup)
        self.assertIn('visudo -cf "${SUDOERS_FILE}"', setup)
        self.assertIn('grep -Fxq "${API_USER} ALL=(root) NOPASSWD:', setup)

    def test_firewall_is_fixed_port_explicit_source_and_default_deny(self):
        firewall = FIREWALL.read_text(encoding="utf-8")
        policy = POLICY.read_text(encoding="utf-8")
        self.assertIn('port_text != "9443"', firewall)
        self.assertIn('allowed_interfaces = {"eth0", "wg-pcs", "wlan0"}', firewall)
        self.assertIn("WireGuard API sources must be explicit IPv4 /32s", firewall)
        self.assertIn("every WireGuard API source must be an approved", firewall)
        self.assertIn('comment "pcs-api-default-deny"', firewall)
        self.assertNotIn("wwan0=", policy)
        self.assertNotIn("0.0.0.0/0", policy)

    def test_tls_import_checks_expiry_key_match_and_every_san(self):
        setup = SETUP.read_text(encoding="utf-8")
        self.assertIn("-checkend 86400", setup)
        self.assertIn('pkey -in "${key}" -noout -check', setup)
        self.assertIn("certificate and private key do not match", setup)
        self.assertIn("-checkip", setup)
        self.assertIn("-checkhost", setup)
        self.assertIn('sudo -u "${API_USER}" test -f "${certificate}"', setup)
        self.assertIn('sudo -u "${API_USER}" test -f "${key}"', setup)
        self.assertIn('sudo test ! -L "${certificate}"', setup)
        self.assertIn("PCS_API_CERTIFICATE_IDENTITIES", POLICY.read_text(encoding="utf-8"))

    def test_activation_is_firewall_first_checked_and_reversible(self):
        setup = SETUP.read_text(encoding="utf-8")
        firewall_start = setup.index("sudo systemctl start pcs-stats-api-firewall.service")
        api_start = setup.index("sudo systemctl start pcs-stats-api.service")
        enable = setup.index("sudo systemctl enable pcs-stats-api-firewall.service")
        self.assertLess(firewall_start, api_start)
        self.assertLess(api_start, enable)
        self.assertIn("if ! check_feature", setup)
        self.assertIn("deactivate_feature", setup[api_start:])
        self.assertIn("preserving policy, TLS, and token data", setup)

    def test_installed_recovery_helper_survives_temporary_staging(self):
        setup = SETUP.read_text(encoding="utf-8")
        self.assertIn('SETUP_DST="/usr/local/sbin/pcs-stats-api-setup"', setup)
        self.assertIn('install -o root -g root -m 0755 "${BASH_SOURCE[0]}" "${SETUP_DST}"', setup)
        self.assertIn('sudo test -x "${SETUP_DST}"', setup)
        self.assertIn('firewall_validator="${FIREWALL_DST}"', setup)
        self.assertIn('"${APPLICATION_DST}" "${SETUP_DST}"', setup)

    def test_token_store_is_outside_git_and_owner_is_preserved(self):
        helper = TOKEN_HELPER.read_text(encoding="utf-8")
        setup = SETUP.read_text(encoding="utf-8")
        self.assertIn('/etc/pcs-stats-api/api-read-tokens.json', helper)
        self.assertIn('TOKEN_FILE="${CONFIG_DIR}/api-read-tokens.json"', setup)
        self.assertIn('PASSWORD_HELPER_DST="/usr/local/sbin/pcs-admin-password-helper"', setup)
        self.assertIn('install the PCS control-panel password helper first', setup)
        self.assertIn('password helper must be root:root mode 0755', setup)
        self.assertNotIn('"${PASSWORD_HELPER_DST}" "${FIREWALL_DST}"', setup)
        self.assertIn("previous_owner", helper)
        self.assertIn("os.chown", helper)
        self.assertIn('chown root:"${API_GROUP}"', setup)

    def test_config_directory_is_traversable_but_sensitive_files_stay_restricted(self):
        setup = SETUP.read_text(encoding="utf-8")
        self.assertIn('install -d -o root -g root -m 0755 "${CONFIG_DIR}"', setup)
        self.assertIn('install -d -o root -g "${API_GROUP}" -m 0750 "${TLS_DIR}"', setup)
        self.assertIn('install -o root -g "${API_GROUP}" -m 0640 "${key}"', setup)
        self.assertIn('chmod 0640 "${TOKEN_FILE}"', setup)

    def test_pcs_self_test_covers_deployed_api_without_requiring_it(self):
        self_test = (ROOT / "scripts" / "pcs-self-test.sh").read_text(encoding="utf-8")
        self.assertIn('section "PCS Stats API"', self_test)
        self.assertIn('skip "PCS Stats API has no imported deployment policy"', self_test)
        self.assertIn("pcs-stats-api-firewall.service", self_test)
        self.assertIn("pcs-stats-api.service", self_test)
        self.assertIn('PCS_STATS_API_PORT="9443"', self_test)
        self.assertIn("PCS Stats API public response passes the local redaction check", self_test)
        self.assertIn('data["details"] is None', self_test)

    def test_every_setup_command_is_documented(self):
        setup = SETUP.read_text(encoding="utf-8")
        documentation = DOC.read_text(encoding="utf-8")
        script_doc = SCRIPT_DOC.read_text(encoding="utf-8")
        commands = {
            "--prepare", "--validate-policy", "--import-policy",
            "--validate-tls", "--import-tls", "--issue-token",
            "--revoke-token", "--activate", "--check", "--deactivate",
            "--rollback", "--help",
        }
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, setup)
                self.assertIn(command, documentation)
                self.assertIn(command, script_doc)

    @unittest.skipIf(os.name == "nt", "Bash policy validation runs in Linux CI")
    def test_example_policy_passes_bash_validator(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["PCS_WIREGUARD_CONFIG"] = str(Path(directory) / "absent-wireguard.conf")
            result = subprocess.run(
                ["bash", str(SETUP), "--validate-policy", str(POLICY)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("policy is valid", result.stdout)

    @unittest.skipIf(os.name == "nt", "Bash firewall validation runs in Linux CI")
    def test_firewall_rejects_broad_wireguard_or_wwan_source(self):
        original = POLICY.read_text(encoding="utf-8")
        invalid = {
            "broad WireGuard": original.replace("wg-pcs=10.77.0.1/32", "wg-pcs=10.77.0.0/24"),
            "cellular": original.replace("wg-pcs=10.77.0.1/32", "wwan0=10.77.0.1/32"),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.conf"
            for label, content in invalid.items():
                with self.subTest(label=label):
                    path.write_text(content, encoding="utf-8")
                    env = os.environ.copy()
                    env["PCS_STATS_API_CONFIG"] = str(path)
                    env["PCS_WIREGUARD_CONFIG"] = str(Path(directory) / "absent-wireguard.conf")
                    result = subprocess.run(
                        ["bash", str(FIREWALL), "--validate-config"],
                        cwd=ROOT,
                        env=env,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)

    @unittest.skipIf(os.name == "nt", "OpenSSL/Bash certificate validation runs in Linux CI")
    def test_tls_validator_accepts_matching_required_sans_and_rejects_missing_san(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            key = temp / "server.key"
            valid_cert = temp / "valid.crt"
            invalid_cert = temp / "invalid.crt"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-days", "2", "-subj", "/CN=pcs.local",
                "-addext", "subjectAltName=DNS:pcs.local,IP:10.42.0.1",
                "-keyout", str(key), "-out", str(valid_cert),
            ], check=True, capture_output=True, text=True)
            subprocess.run([
                "openssl", "req", "-x509", "-key", str(key), "-days", "2",
                "-subj", "/CN=wrong.local",
                "-addext", "subjectAltName=DNS:wrong.local,IP:10.42.0.1",
                "-out", str(invalid_cert),
            ], check=True, capture_output=True, text=True)

            env = os.environ.copy()
            env["PCS_WIREGUARD_CONFIG"] = str(temp / "absent-wireguard.conf")

            valid = subprocess.run(
                ["bash", str(SETUP), "--validate-tls", str(valid_cert), str(key), str(POLICY)],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            missing_san = subprocess.run(
                ["bash", str(SETUP), "--validate-tls", str(invalid_cert), str(key), str(POLICY)],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(missing_san.returncode, 0)
            self.assertIn("missing required DNS identity: pcs.local", missing_san.stderr)


if __name__ == "__main__":
    unittest.main()
