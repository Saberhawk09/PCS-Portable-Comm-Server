import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TimeSourceHierarchyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrony_setup = (ROOT / "scripts" / "setup-chrony-lan-ntp.sh").read_text(encoding="utf-8")
        cls.gps_setup = (ROOT / "scripts" / "setup-wwan-gps-nmea.sh").read_text(encoding="utf-8")
        cls.rtc_setup = (ROOT / "scripts" / "setup-rtc.sh").read_text(encoding="utf-8")
        cls.rtc_seed = (ROOT / "scripts" / "pcs-rtc-seed.sh").read_text(encoding="utf-8")
        cls.rtc_unit = (ROOT / "systemd" / "pcs-rtc-seed.service").read_text(encoding="utf-8")
        cls.web_action = (ROOT / "scripts" / "pcs-web-action.sh").read_text(encoding="utf-8")
        cls.failover_test = (ROOT / "scripts" / "test-time-source-failover.sh").read_text(encoding="utf-8")

    def test_gps_is_preferred_but_not_blindly_trusted(self):
        gps_line = next(
            line for line in self.gps_setup.splitlines()
            if line.startswith("refclock SHM 0 refid GPS")
        )
        self.assertIn(" prefer", gps_line)
        self.assertNotIn(" trust", gps_line)

    def test_internet_and_local_holdover_are_repeatable(self):
        self.assertIn("pool pool.ntp.org iburst maxsources 4", self.chrony_setup)
        self.assertIn("local stratum 10", self.chrony_setup)
        self.assertIn("rtcsync", self.chrony_setup)
        self.assertIn("allow ${PCS_NTP_NETWORK}", self.chrony_setup)

    def test_rtc_seed_runs_before_chrony_and_is_installed(self):
        self.assertIn("Before=chrony.service", self.rtc_unit)
        self.assertIn("ExecStart=/usr/local/sbin/pcs-rtc-seed", self.rtc_unit)
        self.assertIn("systemctl enable pcs-rtc-seed.service", self.rtc_setup)
        self.assertIn('install -o root -g root -m 0755 "${RTC_SEED_SRC}"', self.rtc_setup)

    def test_rtc_seed_validates_before_setting_system_clock(self):
        self.assertIn("MINIMUM_RTC_EPOCH=1704067200", self.rtc_seed)
        self.assertIn("MAXIMUM_RTC_EPOCH=4102444800", self.rtc_seed)
        self.assertIn('--hctosys --utc', self.rtc_seed)
        self.assertIn('--check', self.rtc_seed)

    def test_rtc_seed_retries_transient_boot_read_failures(self):
        self.assertIn('RTC_READ_ATTEMPTS="${PCS_RTC_READ_ATTEMPTS:-10}"', self.rtc_seed)
        self.assertIn('for _attempt in $(seq 1 "${RTC_READ_ATTEMPTS}")', self.rtc_seed)
        self.assertIn('RTC_SEED_ATTEMPTS="${PCS_RTC_SEED_ATTEMPTS:-10}"', self.rtc_seed)
        self.assertIn('for _attempt in $(seq 1 "${RTC_SEED_ATTEMPTS}")', self.rtc_seed)
        self.assertIn("After=local-fs.target fake-hwclock.service", self.rtc_unit)
        self.assertNotIn("dev-rtc0.device", self.rtc_unit)

    def test_dashboard_uses_selected_reference_for_gnss_label(self):
        self.assertIn('chrony_selected_gps = "(GPS)" in chrony_ref.upper()', self.web_action)
        public_source_block = self.web_action.split('"source": (', 1)[1].split('),', 1)[0]
        self.assertIn("chrony_selected_gps", public_source_block)
        self.assertNotIn("chrony_gps_source_present", public_source_block)

    def test_admin_rtc_write_rejects_local_holdover(self):
        self.assertIn("RTC write skipped: Chrony has no authoritative", self.web_action)
        self.assertIn("^Reference ID", self.web_action)
        self.assertIn("7F7F0101", self.web_action)

    def test_failover_test_is_explicit_and_always_restores_sources(self):
        self.assertIn('"${1:-}" != "--run"', self.failover_test)
        self.assertIn("trap restore_normal_selection EXIT", self.failover_test)
        self.assertIn("chronyc online", self.failover_test)
        self.assertIn("chronyc selectopts GPS -noselect", self.failover_test)
        self.assertIn("chronyc reset sources", self.failover_test)
        self.assertIn("chronyc burst 4/4", self.failover_test)
        self.assertIn('wait_for_dashboard_source "Internet NTP"', self.failover_test)
        self.assertIn('wait_for_dashboard_source "RTC holdover"', self.failover_test)
        self.assertIn("GPS was not reselected after restoration", self.failover_test)


if __name__ == "__main__":
    unittest.main()
