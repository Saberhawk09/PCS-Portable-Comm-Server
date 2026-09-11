import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pcs_reinstall_restore", ROOT / "scripts/pcs_reinstall_restore.py")
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "backup"
        self.root.mkdir()

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
        return target

    def network(self):
        self.write("etc/pcs/wireguard-management.conf", 'PCS_WG_ALLOWED_IPS="10.6.0.1/32,10.6.0.4/32"\nPCS_WG_ADMIN_SOURCES="10.6.0.4/32"\nPCS_WG_USE_PRESHARED_KEY=no\n')
        self.write("etc/pcs/wireguard/private.key", "saved-key\n")

    def test_network_keeps_saved_policy_and_selects_only_wifi(self):
        self.network()
        self.write("etc/NetworkManager/system-connections/home.nmconnection", "[connection]\ntype=wifi\n[ wifi-security ]\npsk=secret\n")
        self.write("etc/NetworkManager/system-connections/cell.nmconnection", "[connection]\ntype=gsm\n")
        plan = restore.plan_restore(self.root, "network")
        self.assertEqual({p[0] for p in plan}, {"etc/pcs/wireguard-management.conf", "etc/pcs/wireguard/private.key", "etc/NetworkManager/system-connections/home.nmconnection"})
        self.assertEqual(plan[0][1], 0o600)

    def test_partial_vpn_backup_fails_before_copy(self):
        self.network()
        (self.root / "etc/pcs/wireguard/private.key").unlink()
        with self.assertRaisesRegex(ValueError, "missing backup file"):
            restore.plan_restore(self.root, "network")

    def test_partial_api_identity_fails(self):
        self.write("etc/pcs-stats-api/policy.conf", "saved policy")
        with self.assertRaisesRegex(ValueError, "missing backup file"):
            restore.plan_restore(self.root, "api")

    def test_api_restores_identity_and_pairings_without_runtime_or_lock(self):
        for name in ("policy.conf", "tls/server.crt", "tls/server.key", "api-read-tokens.json"):
            self.write("etc/pcs-stats-api/" + name, "original")
        self.write("etc/pcs-stats-api/runtime.env", "stale paths")
        plan = restore.plan_restore(self.root, "api")
        self.assertEqual(len(plan), 4)
        self.assertNotIn("etc/pcs-stats-api/runtime.env", [p[0] for p in plan])
        self.assertIn(("etc/pcs-stats-api/tls/server.key", 0o640, "pcs-api"), plan)

    def test_policy_rejects_shell_execution_and_duplicates(self):
        self.network()
        for content in ('PCS_WG_ENDPOINT=$(touch /tmp/unwanted)', 'PCS_WG_ALLOWED_IPS=one\nPCS_WG_ALLOWED_IPS=two'):
            self.write("etc/pcs/wireguard-management.conf", content)
            with self.assertRaises(ValueError):
                restore.plan_restore(self.root, "network")

    def test_empty_backup_does_not_enable_features(self):
        self.assertEqual(restore.plan_restore(self.root, "network"), [])
        self.assertEqual(restore.plan_restore(self.root, "api"), [])

    @unittest.skipIf(os.name == "nt", "Unix ownership/symlink semantics")
    def test_symlink_cannot_escape_backup(self):
        self.network()
        key = self.root / "etc/pcs/wireguard/private.key"
        key.unlink()
        key.symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            restore.plan_restore(self.root, "network")

    @unittest.skipIf(os.name == "nt", "Unix ownership semantics")
    def test_restore_preserves_full_policy_and_refuses_conflict_before_copy(self):
        self.network()
        destination = Path(self.temp.name) / "live"
        with patch.object(restore.os, "chown"):
            self.assertEqual(restore.restore(self.root, "network", destination), 2)
            self.assertEqual(restore.restore(self.root, "network", destination), 2)
        policy = destination / "etc/pcs/wireguard-management.conf"
        self.assertEqual(policy.read_bytes(), (self.root / "etc/pcs/wireguard-management.conf").read_bytes())
        key = destination / "etc/pcs/wireguard/private.key"
        key.write_text("different key")
        with patch.object(restore.os, "chown"):
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                restore.restore(self.root, "network", destination)
        self.assertEqual(key.read_text(), "different key")

    @unittest.skipIf(os.name == "nt", "Bash runs in Linux validation")
    def test_public_api_check_cannot_pass_failed_curl_or_bad_json(self):
        source = (ROOT / "scripts/setup-pcs-stats-api.sh").read_text()
        function = source[source.index("check_public_response() {"):source.index("\ncheck_feature() {")]
        for output, code, succeeds in (("", 28, False), ('{"access":"public","details":null}', 28, False), ('{"access":"public","details":null}', 0, True), ('{"access":"public","details":{"secret":1}}', 0, False)):
            with self.subTest(output=output, code=code):
                script = "set -euo pipefail\nPCS_API_PORT=9443\n" + function + "\ncurl() { printf '%s' \"$OUTPUT\"; return \"$CODE\"; }\nif check_public_response; then exit 0; else exit 9; fi\n"
                result = subprocess.run(["bash", "-c", script], env={**os.environ, "OUTPUT": output, "CODE": str(code)}, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)

    def test_base_recovery_orders_network_before_vpn_and_api_after_panel(self):
        source = (ROOT / "scripts/setup-pcs-base.sh").read_text()
        self.assertLess(source.index('setup-pcs-reinstall-restore.sh --network'), source.index('if [[ "${PCS_SETUP_WIREGUARD}" == "yes" ]]; then\n    WIREGUARD_PROFILE_PATH'))
        self.assertLess(source.index('run_step "Install PCS Control Panel"'), source.index('setup-pcs-reinstall-restore.sh --api'))
        self.assertIn('[[ "${PCS_WIREGUARD_RESTORED}" != "yes" ]] && ! PCS_WIREGUARD_IMPORT_REPLACE_CONFIRM', source)


if __name__ == "__main__":
    unittest.main()
