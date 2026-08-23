import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "setup-pistar-pcs.sh"
DOC = ROOT / "docs" / "pi-star-integration.md"


class PiStarPcsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_as_built_usb_ethernet_profile_is_the_default(self):
        self.assertIn('PCS_PISTAR_INTERFACE="${PCS_PISTAR_INTERFACE:-eth0}"', self.source)
        self.assertIn(
            'PCS_PISTAR_ETHERNET_DRIVER="${PCS_PISTAR_ETHERNET_DRIVER:-r8152}"',
            self.source,
        )
        self.assertIn('PCS_PISTAR_DISABLE_WIFI="${PCS_PISTAR_DISABLE_WIFI:-yes}"', self.source)
        self.assertIn('PCS_PISTAR_WIFI_INTERFACE="${PCS_PISTAR_WIFI_INTERFACE:-wlan0}"', self.source)
        self.assertIn(
            'PCS_PISTAR_LINK_STABILITY_SECONDS="${PCS_PISTAR_LINK_STABILITY_SECONDS:-30}"',
            self.source,
        )

    def test_wired_preflight_precedes_every_apply_mutation(self):
        preflight_call = self.source.index("\nvalidate_wired_preflight\n")
        root_remount = self.source.index('sudo mount -o remount,rw /', preflight_call)

        self.assertLess(preflight_call, root_remount)
        for evidence in (
            'is not backed by a USB device',
            'PCS_PISTAR_ETHERNET_DRIVER',
            '/carrier',
            'ping -I "${PCS_PISTAR_INTERFACE}"',
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, self.source)

    def test_apply_requires_a_continuously_stable_wired_path(self):
        for evidence in (
            'for ((second = 1; second <= PCS_PISTAR_LINK_STABILITY_SECONDS; second++))',
            'lost Ethernet carrier during the stability window',
            'lost PCS reachability during the stability window',
            '/carrier_changes',
            'carrier changed during the stability window',
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, self.source)

    def test_wifi_disable_uses_recoverable_boot_overlay(self):
        self.assertIn('dtoverlay=disable-wifi', self.source)
        self.assertIn('WIFI_BLOCK_BEGIN="# BEGIN PCS HOTSPOT WIFI"', self.source)
        self.assertIn('PCS_PISTAR_DISABLE_WIFI=no', DOC.read_text(encoding="utf-8"))
        self.assertNotIn("wpa_supplicant.conf", self.source)
        self.assertNotIn("denyinterfaces", self.source)

    def test_root_and_boot_mounts_are_backed_up_and_restored_read_only(self):
        self.assertIn('findmnt -no OPTIONS /boot', self.source)
        self.assertIn('sudo mount -o remount,rw /boot', self.source)
        self.assertIn('remount_boot_read_only', self.source)
        self.assertIn('"${BOOT_CONFIG}"', self.source)
        self.assertIn('sudo cp -a "${path}" "${backup_dir}/"', self.source)

    def test_final_check_requires_wired_address_route_and_absent_wifi_device(self):
        for evidence in (
            'PCS_PISTAR_INTERFACE} has ${PCS_PISTAR_ADDRESS}',
            'Default route uses ${PCS_PISTAR_GATEWAY} on ${PCS_PISTAR_INTERFACE}',
            'Onboard Wi-Fi hardware is disabled',
            'No IPv4 address is assigned to ${PCS_PISTAR_WIFI_INTERFACE}',
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, self.source)

    def test_route_and_address_checks_do_not_false_fail_under_pipefail(self):
        self.assertIn('default_routes="$(ip route show default', self.source)
        self.assertIn('<<<"${default_routes}"', self.source)
        self.assertIn('interface_addresses="$(ip -4 address show', self.source)
        self.assertNotIn('ip route show default 2>/dev/null | grep -Eq', self.source)


if __name__ == "__main__":
    unittest.main()
