import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which("bash"), "requires bash")
class UplinkSelfTest(unittest.TestCase):
    def run_network(self, default, managed):
        source = (ROOT / "scripts/pcs-self-test.sh").read_text()
        section = source.split('section "Network"', 1)[1].split('if ip route show 10.42.0.0/24', 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in {
                "python3": 'cat >/dev/null; printf "%s" "$TEST_MANAGED"',
                "nmcli": 'echo "eth0:connected:pcs-router-wan-share"',
                "ip": 'if [ "$1" = route ]; then [ -z "$TEST_DEFAULT" ] || echo "default via 192.0.2.1 dev $TEST_DEFAULT"; else echo "inet 10.42.0.1/24"; fi',
            }.items():
                p = Path(tmp) / name
                p.write_text("#!/bin/sh\n" + body + "\n")
                p.chmod(0o755)
            prelude = 'PCS_WIFI_IFACE=wlan0; PCS_ETH_IFACE=eth0; PCS_LAN_PROFILE=pcs-router-wan-share; PCS_ETH_ADDR=10.42.0.1/24; pass() { echo "PASS $*"; }; warn() { echo "WARN $*"; }; fail() { echo "FAIL $*"; };'
            result = subprocess.run(["bash", "-c", prelude + section], text=True, capture_output=True,
                env={**os.environ, "PATH": tmp + os.pathsep + os.environ["PATH"], "TEST_DEFAULT": default, "TEST_MANAGED": managed}, check=True)
            return result.stdout

    def test_configured_ethernet_names_are_accepted(self):
        for name in ("eth1", "enx001122334455", "eth0.20"):
            output = self.run_network(name, name)
            self.assertIn("PASS Default route uses configured WAN " + name, output)
            self.assertNotIn("WARN", output)

    def test_unknown_default_still_warns(self):
        self.assertIn("WARN Default route uses unexpected interface", self.run_network("mystery0", "enx001122334455"))

    def test_offline_is_informational(self):
        output = self.run_network("", "")
        self.assertNotIn("WARN", output)
        self.assertIn("offline LAN only", output)

    @unittest.skipIf(os.name == 'nt', 'requires POSIX file permissions')
    def test_private_config_uses_manager_and_detects_controller_failures(self):
        source = (ROOT / 'scripts/pcs-self-test.sh').read_text()
        section = source.split('PCS_CELLULAR_PROFILE_ACTIVE="$(pcs_cellular_profile_name)"', 1)[1].split('if nmcli -t -f NAME', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'uplinks.json'
            config.write_text('{}')
            config.chmod(0)
            section = section.replace('/etc/pcs/uplinks.json', str(config))
            section = section.replace('sudo -n /usr/local/sbin/pcs-uplink-manager --check', 'true')
            prelude = 'pass() { echo "PASS $*"; }; warn() { echo "WARN $*"; }; fail() { echo "FAIL $*"; }; '
            for active, enabled, legacy in [(True, True, False), (False, True, False), (True, False, False), (True, True, True)]:
                functions = 'service_active() { [ "$1" = pcs-uplink-manager.service ] && ' + ('true' if active else 'false') + '; }; '
                functions += 'service_enabled() { if [ "$1" = pcs-uplink-manager.service ]; then ' + ('true' if enabled else 'false') + '; else ' + ('true' if legacy else 'false') + '; fi; }; '
                result = subprocess.run(['bash', '-c', prelude + functions + section], capture_output=True, text=True, check=True)
                self.assertIn('Generalized uplink configuration is valid', result.stdout)
                self.assertEqual('FAIL' in result.stdout, not active or not enabled or legacy)
            config.chmod(0o600)

    def test_managed_interface_detection_uses_cache_without_reading_private_config(self):
        import contextlib
        import io
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        source = (ROOT / 'scripts/pcs-self-test.sh').read_text().split("<<'PYUPLINK'", 1)[1].split('\n', 1)[1].split('\nPYUPLINK', 1)[0]
        for available in (True, False):
            module = SimpleNamespace(CONFIG=SimpleNamespace(exists=lambda: True),
                cached_status=lambda: {'available': available, 'uplinks': [{'interface': 'eth0.20', 'link': True, 'address': True}]})
            output = io.StringIO()
            with patch.dict(sys.modules, {'pcs_uplink_manager': module}), contextlib.redirect_stdout(output):
                exec(source, {})
            self.assertEqual(output.getvalue().strip(), 'eth0.20' if available else '')
