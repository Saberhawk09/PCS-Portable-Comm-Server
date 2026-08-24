import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "pcs_cellular_fallback.py"
SPEC = importlib.util.spec_from_file_location("pcs_cellular_fallback", MODULE_PATH)
fallback = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fallback
SPEC.loader.exec_module(fallback)


class FakeNetworkManager:
    def __init__(self, *, wifi=False, cellular=False):
        self.wifi = wifi
        self.cellular = cellular
        self.connect_calls = 0
        self.disconnect_calls = 0

    def wifi_active(self, _interface):
        return self.wifi

    def cellular_active(self, _profile):
        return self.cellular

    def connect_cellular(self, _profile):
        self.connect_calls += 1
        self.cellular = True
        return True, "connected"

    def disconnect_cellular(self, _profile):
        self.disconnect_calls += 1
        self.cellular = False
        return True, "disconnected"


class ConfigTests(unittest.TestCase):
    def test_config_is_loaded_and_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fallback.conf"
            path.write_text(
                "[fallback]\n"
                "wifi_interface=wlan9\n"
                "cellular_profile=field-cell\n"
                "poll_seconds=5\n"
                "wifi_loss_seconds=20\n"
                "wifi_recovery_seconds=40\n",
                encoding="utf-8",
            )
            config = fallback.load_config(path)

        self.assertEqual(config.wifi_interface, "wlan9")
        self.assertEqual(config.cellular_profile, "field-cell")
        self.assertEqual(config.poll_seconds, 5)
        self.assertEqual(config.wifi_loss_seconds, 20)
        self.assertEqual(config.wifi_recovery_seconds, 40)

    def test_zero_poll_interval_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fallback.conf"
            path.write_text(
                "[fallback]\npoll_seconds=0\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "at least 1"):
                fallback.load_config(path)


class ControllerTests(unittest.TestCase):
    def make_controller(self, directory, network_manager, *, loss=30, recovery=30):
        config = fallback.FallbackConfig(
            wifi_interface="wlan0",
            cellular_profile="pcs-cellular-profile",
            poll_seconds=10,
            wifi_loss_seconds=loss,
            wifi_recovery_seconds=recovery,
        )
        marker = Path(directory) / "owned"
        return fallback.FallbackController(config, network_manager, marker), marker

    def test_sustained_wifi_loss_connects_and_records_ownership(self):
        with tempfile.TemporaryDirectory() as temporary:
            network_manager = FakeNetworkManager(wifi=False, cellular=False)
            controller, marker = self.make_controller(temporary, network_manager)

            self.assertIn("waiting", controller.step(now=100))
            self.assertIn("still running", controller.step(now=129))
            self.assertIn("connected cellular", controller.step(now=130))

            self.assertEqual(network_manager.connect_calls, 1)
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), "pcs-cellular-profile")

    def test_restored_wifi_disconnects_only_owned_session_after_stability(self):
        with tempfile.TemporaryDirectory() as temporary:
            network_manager = FakeNetworkManager(wifi=False, cellular=False)
            controller, marker = self.make_controller(temporary, network_manager)
            controller.step(now=100)
            controller.step(now=130)

            network_manager.wifi = True
            self.assertIn("waiting", controller.step(now=200))
            self.assertIn("still running", controller.step(now=229))
            self.assertIn("disconnected", controller.step(now=230))

            self.assertEqual(network_manager.disconnect_calls, 1)
            self.assertFalse(marker.exists())

    def test_manual_cellular_session_is_never_disconnected(self):
        with tempfile.TemporaryDirectory() as temporary:
            network_manager = FakeNetworkManager(wifi=True, cellular=True)
            controller, marker = self.make_controller(temporary, network_manager)

            message = controller.step(now=100)
            controller.step(now=200)

            self.assertIn("manual control", message)
            self.assertEqual(network_manager.disconnect_calls, 0)
            self.assertFalse(marker.exists())

    def test_stale_ownership_is_cleared_without_disconnect(self):
        with tempfile.TemporaryDirectory() as temporary:
            network_manager = FakeNetworkManager(wifi=True, cellular=False)
            controller, marker = self.make_controller(temporary, network_manager)
            marker.write_text("pcs-cellular-profile\n", encoding="utf-8")

            self.assertIn("cleared stale", controller.step(now=100))
            self.assertFalse(marker.exists())
            self.assertEqual(network_manager.disconnect_calls, 0)

    def test_failed_activation_does_not_claim_ownership(self):
        class FailingNetworkManager(FakeNetworkManager):
            def connect_cellular(self, _profile):
                self.connect_calls += 1
                return False, "modem unavailable"

        with tempfile.TemporaryDirectory() as temporary:
            network_manager = FailingNetworkManager(wifi=False, cellular=False)
            controller, marker = self.make_controller(temporary, network_manager, loss=0)
            controller.step(now=100)
            message = controller.step(now=100)

            self.assertIn("activation failed", message)
            self.assertFalse(marker.exists())


class InstallerTests(unittest.TestCase):
    def test_profile_remains_non_autoconnecting_in_both_modes(self):
        source = (ROOT / "scripts" / "setup-cellular-profile.sh").read_text(encoding="utf-8")
        self.assertIn("connection.autoconnect no", source)
        self.assertIn("--fallback MODE", source)
        self.assertIn("pcs-cellular-fallback.service", source)

    def test_base_installer_records_fallback_choice(self):
        source = (ROOT / "scripts" / "setup-pcs-base.sh").read_text(encoding="utf-8")
        self.assertIn('PCS_CELLULAR_FALLBACK_MODE_DEFAULT="manual"', source)
        self.assertIn("Automatically use cellular when Wi-Fi is unavailable?", source)
        self.assertIn('printf "PCS_CELLULAR_FALLBACK_MODE=%q', source)

    def test_systemd_service_starts_at_boot_without_waiting_for_network_online(self):
        source = (ROOT / "systemd" / "pcs-cellular-fallback.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("WantedBy=multi-user.target", source)
        self.assertIn("After=NetworkManager.service", source)
        self.assertIn("Restart=always", source)
        self.assertNotIn("network-online.target", source)


if __name__ == "__main__":
    unittest.main()
