import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "scripts" / "pcs_sa818.py"
SPEC = importlib.util.spec_from_file_location("pcs_sa818", MODULE_PATH)
sa818 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = sa818
SPEC.loader.exec_module(sa818)


class Sa818Tests(unittest.TestCase):
    def test_commissioned_radio_settling_delay_is_preserved(self):
        self.assertEqual(sa818.COMMAND_SETTLE_SECONDS, 0.4)

    def test_commissioned_profile_generates_exact_programming_commands(self):
        profile = sa818.RadioProfile()

        commands = [command for command, _ in profile.programming_commands()]

        self.assertEqual(
            commands,
            [
                "AT+DMOCONNECT",
                "AT+DMOSETGROUP=1,144.5500,144.5500,0000,1,0000",
                "AT+DMOSETVOLUME=8",
                "AT+SETFILTER=1,1,1",
                "AT+SETTAIL=0",
            ],
        )

    def test_filter_codes_follow_sa818s_inverted_semantics(self):
        enabled = sa818.RadioProfile(
            pre_de_emphasis=True,
            high_pass=True,
            low_pass=True,
        )
        filter_command = enabled.programming_commands()[3][0]
        self.assertEqual(filter_command, "AT+SETFILTER=0,0,0")

    def test_group_readback_must_match_every_programmed_field(self):
        profile = sa818.RadioProfile()
        sa818.verify_group(
            "+DMOREADGROUP:1,144.5500,144.5500,0000,1,0000", profile
        )
        with self.assertRaisesRegex(RuntimeError, "group mismatch"):
            sa818.verify_group(
                "+DMOREADGROUP:1,144.5550,144.5550,0000,1,0000", profile
            )

    def test_ini_profile_is_validated(self):
        content = """\
[radio]
device = /dev/serial0
baud = 9600
bandwidth_khz = 25
tx_frequency_mhz = 144.5500
rx_frequency_mhz = 144.5500
tx_tone = 0000
squelch = 1
rx_tone = 0000
volume = 8
pre_de_emphasis = off
high_pass = off
low_pass = off
tx_tail = off
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sa818.ini"
            path.write_text(content, encoding="utf-8")
            profile = sa818.load_profile(path)

        self.assertEqual(profile.expected_group(), "1,144.5500,144.5500,0000,1,0000")


if __name__ == "__main__":
    unittest.main()
