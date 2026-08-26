import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "pcs_direwolf_uplink_recovery.py"
SPEC = importlib.util.spec_from_file_location("pcs_direwolf_uplink_recovery", MODULE_PATH)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    def __init__(self, *, interface="wlan0", connected=True, resolves=True, active=True):
        self.interface = interface
        self.connected = connected
        self.resolves = resolves
        self.active = active
        self.restart_calls = 0

    def run(self, arguments, timeout=20):
        if arguments[:4] == ["ip", "-4", "route", "get"]:
            return Result(stdout=f"1.1.1.1 dev {self.interface} src 192.0.2.10\n")
        if arguments[:3] == ["systemctl", "is-active", "--quiet"]:
            return Result(returncode=0 if self.active else 3)
        if arguments[:2] == ["getent", "ahostsv4"]:
            return Result(returncode=0 if self.resolves else 2)
        if arguments and arguments[0] == "ss":
            output = '0 0 192.0.2.10:40000 198.51.100.8:14580 users:(("direwolf",pid=10,fd=5))\n' if self.connected else ""
            return Result(stdout=output)
        if arguments == ["systemctl", "restart", "direwolf.service"]:
            self.restart_calls += 1
            self.connected = True
            return Result()
        raise AssertionError(arguments)


def write_config(directory: str) -> Path:
    path = Path(directory) / "direwolf.conf"
    path.write_text("IGSERVER noam.aprs2.net\nIGLOGIN N0CALL 12345\n", encoding="utf-8")
    return path


class ParserTests(unittest.TestCase):
    def test_aprs_server_defaults_to_standard_port_without_exposing_login(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(recovery.aprs_is_server(write_config(temporary)), ("noam.aprs2.net", 14580))

    def test_commented_or_invalid_server_is_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "direwolf.conf"
            path.write_text("#IGSERVER example.net\nIGSERVER example.net invalid\n", encoding="utf-8")
            self.assertIsNone(recovery.aprs_is_server(path))


class RecoveryTests(unittest.TestCase):
    def make_recovery(self, directory, runner, *, grace=0, verify=0, cooldown=300):
        return recovery.UplinkRecovery(
            runner,
            config_path=write_config(directory),
            state_path=Path(directory) / "uplink",
            restart_marker=Path(directory) / "last",
            grace_seconds=grace,
            verify_seconds=verify,
            cooldown_seconds=cooldown,
            sleep=lambda _seconds: None,
            monotonic=lambda: 100.0,
            wall_time=lambda: 1000.0,
        )

    def test_first_observation_records_baseline_without_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner(interface="wlan0", connected=False)
            subject = self.make_recovery(temporary, runner)
            ok, message = subject.recover()
            self.assertTrue(ok)
            self.assertIn("Initialized", message)
            self.assertEqual(runner.restart_calls, 0)

    def test_same_default_interface_never_restarts(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner(interface="wlan0", connected=False)
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wlan0\n", encoding="utf-8")
            ok, message = subject.recover()
            self.assertTrue(ok)
            self.assertIn("remains wlan0", message)
            self.assertEqual(runner.restart_calls, 0)

    def test_changed_uplink_allows_native_reconnect_before_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner(interface="wwan0", connected=True)
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wlan0\n", encoding="utf-8")
            ok, message = subject.recover()
            self.assertTrue(ok)
            self.assertIn("reconnected", message)
            self.assertEqual(runner.restart_calls, 0)

    def test_changed_uplink_restarts_only_when_dns_works_and_socket_is_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner(interface="wwan0", connected=False, resolves=True)
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wlan0\n", encoding="utf-8")
            ok, message = subject.recover()
            self.assertTrue(ok)
            self.assertIn("Restarted", message)
            self.assertEqual(runner.restart_calls, 1)

    def test_dns_failure_does_not_trigger_rf_capable_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner(interface="wwan0", connected=False, resolves=False)
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wlan0\n", encoding="utf-8")
            ok, message = subject.recover()
            self.assertTrue(ok)
            self.assertIn("DNS is unavailable", message)
            self.assertEqual(runner.restart_calls, 0)

    def test_restart_cooldown_prevents_repeated_startup_beacons(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner(interface="wwan0", connected=False, resolves=True)
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wlan0\n", encoding="utf-8")
            subject.restart_marker.write_text("900\n", encoding="utf-8")
            ok, message = subject.recover()
            self.assertTrue(ok)
            self.assertIn("cooldown", message)
            self.assertEqual(runner.restart_calls, 0)


class IntegrationSourceTests(unittest.TestCase):
    def test_dispatcher_and_service_are_guarded(self):
        dispatcher = (ROOT / "networkmanager" / "91-pcs-direwolf-uplink-recovery").read_text(encoding="utf-8")
        service = (ROOT / "systemd" / "pcs-direwolf-uplink-recovery.service").read_text(encoding="utf-8")
        self.assertIn("systemctl --no-block start pcs-direwolf-uplink-recovery.service", dispatcher)
        self.assertIn("ExecStart=/usr/local/sbin/pcs-direwolf-uplink-recovery --recover", service)
        self.assertIn("TimeoutStartSec=150", service)


if __name__ == "__main__":
    unittest.main()
