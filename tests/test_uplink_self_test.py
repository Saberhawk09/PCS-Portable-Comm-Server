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
            prelude = 'PCS_WIFI_IFACE=wlan0; PCS_ETH_IFACE=eth0; PCS_ETH_ADDR=10.42.0.1/24; pass() { echo "PASS $*"; }; warn() { echo "WARN $*"; }; fail() { echo "FAIL $*"; };'
            result = subprocess.run(["bash", "-c", prelude + section], text=True, capture_output=True,
                env={**os.environ, "PATH": tmp + os.pathsep + os.environ["PATH"], "TEST_DEFAULT": default, "TEST_MANAGED": managed}, check=True)
            return result.stdout

    def test_configured_ethernet_names_are_accepted(self):
        for name in ("eth1", "enx001122334455"):
            output = self.run_network(name, name)
            self.assertIn("PASS Default route uses configured WAN " + name, output)
            self.assertNotIn("WARN", output)

    def test_unknown_default_still_warns(self):
        self.assertIn("WARN Default route uses unexpected interface", self.run_network("mystery0", "enx001122334455"))

    def test_offline_is_informational(self):
        output = self.run_network("", "")
        self.assertNotIn("WARN", output)
        self.assertIn("offline LAN only", output)
