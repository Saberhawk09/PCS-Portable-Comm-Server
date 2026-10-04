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
        self.lifecycle = []
        self.socket_reads = 0
        self.snapshots = None
        self.after_restart = None

    def run(self, arguments, timeout=20):
        if arguments[0] == "ip":
            if not self.interface:
                return Result(2)
            return Result(stdout=f"1.1.1.1 dev {self.interface} src 192.0.2.10\n")
        if arguments[:3] == ["systemctl", "is-active", "--quiet"]:
            return Result(returncode=0 if self.active else 3)
        if arguments[:2] == ["getent", "ahostsv4"]:
            return Result(returncode=0 if self.resolves else 2)
        if arguments and arguments[0] == "ss":
            self.socket_reads += 1
            if self.restart_calls and self.after_restart is not None:
                return Result(stdout=self.after_restart)
            if self.snapshots is not None:
                return Result(stdout=self.snapshots[min(self.socket_reads - 1, len(self.snapshots) - 1)])
            output = socket(port=40000 + self.restart_calls) if self.connected else ""
            return Result(stdout=output)
        if arguments == ["systemctl", "stop", "direwolf.service"]:
            self.lifecycle.append(('stop', 'direwolf.service'))
            self.active = False
            return Result()
        if arguments == ["systemctl", "start", "pcs-aprs-ptt-safe.service"]:
            self.lifecycle.append(('start', 'pcs-aprs-ptt-safe.service'))
            return Result()
        if arguments == ["systemctl", "start", "direwolf.service"]:
            self.lifecycle.append(('start', 'direwolf.service'))
            self.restart_calls += 1
            self.active = True
            self.connected = True
            return Result()
        raise AssertionError(arguments)


def socket(local="192.0.2.10", port=40000, peer="198.51.100.8", pid=10, inode=100):
    return f'0 0 [{local}]:{port} [{peer}]:14580 users:(("direwolf",pid={pid},fd=5)) ino:{inode}\n'


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
            runner.snapshots = [socket(local="192.0.2.20"), socket(port=40001)]
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
            self.assertEqual(runner.lifecycle, [('stop', 'direwolf.service'), ('start', 'pcs-aprs-ptt-safe.service'), ('start', 'direwolf.service')])

    def test_dns_failure_does_not_trigger_rf_capable_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner(interface="wwan0", connected=False, resolves=False)
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wlan0\n", encoding="utf-8")
            ok, message = subject.recover()
            self.assertTrue(ok)
            self.assertIn("DNS is unavailable", message)
            self.assertEqual(runner.restart_calls, 0)

    def test_operator_stop_during_grace_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner(interface="eth1", connected=False)
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wlan0\n", encoding="utf-8")
            def stopped_during_wait(port, seconds, old, interface):
                runner.active = False
                return False
            subject._wait_for_connection = stopped_during_wait
            ok, message = subject.recover()
            self.assertTrue(ok)
            self.assertIn("leaving it stopped", message)
            self.assertEqual(runner.lifecycle, [])

    def test_failed_stop_does_not_start_guard_or_engine(self):
        class StopFails(FakeRunner):
            def run(self, arguments, timeout=20):
                if arguments == ["systemctl", "stop", "direwolf.service"]:
                    return Result(1, stderr="stop failed")
                return super().run(arguments, timeout)
        with tempfile.TemporaryDirectory() as temporary:
            runner = StopFails(interface="eth1", connected=False)
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wlan0\n", encoding="utf-8")
            ok, message = subject.recover()
            self.assertFalse(ok)
            self.assertIn("stop direwolf.service failed", message)
            self.assertEqual(runner.lifecycle, [])

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

    def test_stale_established_socket_requires_guarded_restart_and_fresh_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner()
            runner.snapshots = [socket(local="28.46.229.87", peer="44.25.16.4")]
            runner.after_restart = socket(port=40001, peer="44.25.16.4", pid=11, inode=101)
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wwan0\n")
            ok, message = subject.recover()
            self.assertTrue(ok)
            self.assertIn("fresh APRS-IS recovery verified", message)
            self.assertEqual(runner.lifecycle, [('stop', 'direwolf.service'), ('start', 'pcs-aprs-ptt-safe.service'), ('start', 'direwolf.service')])

    def test_same_source_old_socket_is_not_fresh_either(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner()
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wwan0\n")
            self.assertTrue(subject.recover()[0])
            self.assertEqual(runner.restart_calls, 1)

    def test_absent_initial_socket_can_connect_during_grace(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner()
            runner.snapshots = ["", socket()]
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wwan0\n")
            self.assertTrue(subject.recover()[0])
            self.assertEqual(runner.restart_calls, 0)

    def test_restart_without_fresh_usable_socket_fails(self):
        for output in ("", socket(), socket(local="28.46.229.87", port=41000)):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as temporary:
                runner = FakeRunner()
                runner.after_restart = output
                subject = self.make_recovery(temporary, runner)
                subject.state_path.write_text("wwan0\n")
                ok, message = subject.recover()
                self.assertFalse(ok)
                self.assertIn("no fresh route-consistent", message)
                self.assertEqual(runner.restart_calls, 1)

    def test_inactive_direwolf_is_not_started(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner(active=False)
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wwan0\n")
            self.assertIn("inactive", subject.recover()[1])
            self.assertEqual(runner.lifecycle, [])

    def test_no_default_uplink_preserves_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner(interface="")
            subject = self.make_recovery(temporary, runner)
            subject.state_path.write_text("wwan0\n")
            self.assertIn("No default", subject.recover()[1])
            self.assertEqual(subject.state_path.read_text(), "wwan0\n")
            self.assertEqual(runner.lifecycle, [])

    def test_grace_polls_until_fresh_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner()
            old = socket(local="28.46.229.87")
            runner.snapshots = [old, old, "", socket(port=40001)]
            subject = self.make_recovery(temporary, runner, grace=15)
            clock = [0]
            subject.monotonic = lambda: clock[0]
            subject.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
            subject.state_path.write_text("wwan0\n")
            self.assertTrue(subject.recover()[0])
            self.assertEqual(clock[0], 10)
            self.assertEqual(runner.lifecycle, [])

    def test_stale_socket_waits_full_grace(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner()
            subject = self.make_recovery(temporary, runner, grace=15)
            clock = [0]
            subject.monotonic = lambda: clock[0]
            subject.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
            subject.state_path.write_text("wwan0\n")
            self.assertTrue(subject.recover()[0])
            self.assertEqual(clock[0], 15)
            self.assertEqual(runner.restart_calls, 1)


class SocketTests(unittest.TestCase):
    def test_ipv4_ipv6_and_state_column(self):
        runner = FakeRunner()
        runner.snapshots = [socket() + "ESTAB " + socket(local="2001:db8::1", peer="2001:db8::2", inode=101)]
        values = recovery.direwolf_connections(runner, 14580)
        self.assertEqual(len(values), 2)
        self.assertEqual({value.local for value in values}, {"192.0.2.10", "2001:db8::1"})

    def test_unrelated_owner_and_local_port_do_not_qualify(self):
        runner = FakeRunner()
        runner.snapshots = [socket().replace('"direwolf"', '"other"') + socket(port=14580).replace(']:14580 users', ']:12345 users')]
        self.assertEqual(recovery.direwolf_connections(runner, 14580), set())

    def test_unknown_socket_inspection_fails_closed(self):
        class Failed(FakeRunner):
            def run(self, arguments, timeout=20):
                if arguments[0] == "ss":
                    return Result(1)
                return super().run(arguments, timeout)
        self.assertIsNone(recovery.direwolf_connections(Failed(), 14580))

    def test_ipv6_uses_its_effective_route_without_dns(self):
        class IPv6(FakeRunner):
            def run(self, arguments, timeout=20):
                if arguments == ["ip", "-6", "route", "get", "2001:db8::2"]:
                    return Result(stdout="2001:db8::2 dev wwan0 src 2001:db8::1")
                raise AssertionError(arguments)
        connection = recovery.Connection("2001:db8::1", 40000, "2001:db8::2", 14580, 10, "100")
        self.assertTrue(recovery.connection_route(IPv6(), connection, "wlan0")[0])

    def test_route_must_match_source_and_selected_ipv4_interface(self):
        connection = recovery.Connection("192.0.2.10", 40000, "198.51.100.8", 14580, 10, "100")
        self.assertFalse(recovery.connection_route(FakeRunner(interface="eth1"), connection, "wlan0")[0])
        self.assertFalse(recovery.connection_route(FakeRunner(interface=""), connection, "wlan0")[0])


class IntegrationSourceTests(unittest.TestCase):
    def test_dispatcher_and_service_are_guarded(self):
        dispatcher = (ROOT / "networkmanager" / "91-pcs-direwolf-uplink-recovery").read_text(encoding="utf-8")
        service = (ROOT / "systemd" / "pcs-direwolf-uplink-recovery.service").read_text(encoding="utf-8")
        self.assertIn("systemctl --no-block start pcs-direwolf-uplink-recovery.service", dispatcher)
        self.assertIn("ExecStart=/usr/local/sbin/pcs-direwolf-uplink-recovery --recover", service)
        self.assertIn("TimeoutStartSec=300", service)


if __name__ == "__main__":
    unittest.main()
