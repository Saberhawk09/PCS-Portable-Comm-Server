import importlib.util
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pcs_power_monitor", ROOT / "scripts" / "pcs_power_monitor.py")
power = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = power
spec.loader.exec_module(power)


class FakeBus:
    def __init__(self, words=None):
        self.words = words or {}
        self.writes = []
    def read_word_data(self, address, register):
        return power.swap16(self.words[(address, register)])
    def write_word_data(self, address, register, value):
        self.writes.append((address, register, power.swap16(value)))
    def close(self):
        pass


def config():
    return {
        "source_mode": "12v", "low_voltage_threshold": 11.5,
        "low_voltage_hysteresis": 0.3, "confirm_samples": 3,
        "shutdown_delay_seconds": 90, "allow_shutdown": False,
        "monitors": {
            "input": power.MonitorConfig("input", 0x40, 0.01, 10),
            "rail_5v": power.MonitorConfig("rail_5v", 0x41, 0.01, 10),
        },
    }


class PowerTests(unittest.TestCase):
    def test_runtime_directory_preserves_energy_totals_across_service_restart(self):
        service = (ROOT / "systemd" / "pcs-power-monitor.service").read_text(encoding="utf-8")
        self.assertIn("RuntimeDirectoryPreserve=restart", service)

    def test_repository_example_cannot_enable_unverified_calibration(self):
        with self.assertRaises((TypeError, ValueError)):
            power.load_config(ROOT / "config" / "power-monitor.example.json")

    def test_sensor_waits_for_averaged_conversion_after_calibration(self):
        bus = FakeBus()
        pauses = []
        power.Ina226(
            bus,
            power.MonitorConfig("input", 0x40, 0.002, 20),
            sleeper=pauses.append,
        )
        self.assertEqual(pauses, [power.CONVERSION_SETTLE_SECONDS])

    def test_config_requires_unique_addresses(self):
        value = {
            "version": 1, "monitors": {
                "input": {"address": "0x40", "shunt_ohms": 0.01, "max_current_amps": 10},
                "rail_5v": {"address": "0x40", "shunt_ohms": 0.01, "max_current_amps": 10},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "power.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                power.load_config(path)

    def test_input_only_interim_configuration_is_valid(self):
        value = {
            "version": 1,
            "monitors": {
                "input": {"address": "0x40", "shunt_ohms": 0.002, "max_current_amps": 20},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "power.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            loaded = power.load_config(path)
        self.assertEqual(set(loaded["monitors"]), {"input"})
        self.assertFalse(loaded["allow_shutdown"])

    def test_measurements_and_estimate(self):
        # Current LSB is 10/32768 A; power LSB is 25x that.
        bus = FakeBus({
            (0x40, 0x02): 11040, (0x40, 0x04): 3277, (0x40, 0x03): 2621,
            (0x41, 0x02): 4000, (0x41, 0x04): 1638, (0x41, 0x03): 328,
        })
        value = power.collect(bus, config(), power.LowVoltageGuard(config()), 10)
        self.assertAlmostEqual(value["monitors"]["input"]["voltage"], 13.8, places=2)
        self.assertAlmostEqual(value["monitors"]["rail_5v"]["voltage"], 5.0, places=2)
        self.assertGreater(value["estimated_non_5v_power"], 15)
        self.assertIn("conversion losses", value["estimated_non_5v_note"])

    def test_energy_tracker_integrates_charge_and_energy_trapezoidally(self):
        tracker = power.EnergyTracker(["input"], boot_id="boot-1", started_at_epoch=100)
        tracker.update({"input": power.Reading(True, 12.0, 1.0, 10.0)}, 10.0)
        tracker.update({"input": power.Reading(True, 12.0, 3.0, 14.0)}, 3610.0)
        self.assertEqual(tracker.fields("input"), {
            "charge_since_boot_mah": 2000.0,
            "energy_since_boot_wh": 12.0,
        })
        self.assertEqual(tracker.elapsed_seconds, 3600.0)

    def test_energy_tracker_does_not_bridge_an_offline_interval(self):
        tracker = power.EnergyTracker(["input"])
        tracker.update({"input": power.Reading(True, 12.0, 2.0, 20.0)}, 0.0)
        tracker.update({"input": power.Reading(False)}, 3600.0)
        tracker.update({"input": power.Reading(True, 12.0, 2.0, 20.0)}, 7200.0)
        self.assertEqual(tracker.fields("input"), {
            "charge_since_boot_mah": 0.0,
            "energy_since_boot_wh": 0.0,
        })

    def test_energy_tracker_resumes_only_for_same_boot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(json.dumps({
                "energy_tracking": {
                    "boot_id": "boot-1", "started_at_epoch": 100,
                    "elapsed_seconds": 50,
                },
                "monitors": {
                    "input": {
                        "charge_since_boot_mah": 12.5,
                        "energy_since_boot_wh": 0.15,
                    },
                },
            }), encoding="utf-8")
            resumed = power.EnergyTracker.resume(path, ["input"], "boot-1")
            reset = power.EnergyTracker.resume(path, ["input"], "boot-2")
        self.assertEqual(resumed.fields("input")["charge_since_boot_mah"], 12.5)
        self.assertEqual(resumed.started_at_epoch, 100)
        self.assertEqual(reset.fields("input")["charge_since_boot_mah"], 0.0)

    def test_collect_publishes_boot_scoped_energy_fields(self):
        bus = FakeBus({
            (0x40, 0x02): 11040, (0x40, 0x04): 3277, (0x40, 0x03): 2621,
            (0x41, 0x02): 4000, (0x41, 0x04): 1638, (0x41, 0x03): 328,
        })
        tracker = power.EnergyTracker(["input", "rail_5v"], boot_id="boot-1")
        value = power.collect(bus, config(), power.LowVoltageGuard(config()), 10, tracker)
        self.assertEqual(value["energy_tracking"]["scope"], "since_boot")
        self.assertEqual(value["energy_tracking"]["boot_id"], "boot-1")
        self.assertEqual(value["monitors"]["input"]["charge_since_boot_mah"], 0.0)
        self.assertEqual(value["monitors"]["rail_5v"]["energy_since_boot_wh"], 0.0)

    def test_input_only_collection_omits_unavailable_estimate_without_warning(self):
        cfg = config()
        cfg["source_mode"] = "auto"
        cfg["monitors"] = {
            "input": power.MonitorConfig("input", 0x40, 0.002, 20),
        }
        bus = FakeBus({
            (0x40, 0x02): 19168,
            (0x40, 0x04): 1527,
            (0x40, 0x03): 60,
        })
        value = power.collect(bus, cfg, power.LowVoltageGuard(cfg), 10)
        self.assertEqual(value["status"], "ok")
        self.assertEqual(value["configured_roles"], ["input"])
        self.assertIsNone(value["estimated_non_5v_power"])
        self.assertEqual(value["detected_nominal_source"], "24v")

    def test_5v_status_accepts_commissioned_rail_voltage(self):
        monitor = power.MonitorConfig("rail_5v", 0x4C, 0.002, 20)
        self.assertEqual(
            power.reading_status("rail_5v", power.Reading(True, 5.228, 1.565, 8.179), monitor),
            "ok",
        )
        self.assertEqual(
            power.reading_status("rail_5v", power.Reading(True, 5.29, 1.565, 8.275), monitor),
            "ok",
        )
        self.assertEqual(
            power.reading_status("rail_5v", power.Reading(True, 5.31, 1.565, 8.307), monitor),
            "warn",
        )
        self.assertEqual(
            power.reading_status("rail_5v", power.Reading(True, 5.36, 1.565, 8.385), monitor),
            "bad",
        )

    def test_low_voltage_requires_persistence_and_resets_with_hysteresis(self):
        guard = power.LowVoltageGuard(config())
        self.assertEqual(guard.update(11.4, 0), (False, None))
        self.assertEqual(guard.update(11.4, 2), (False, None))
        self.assertEqual(guard.update(11.4, 4), (True, 90))
        self.assertEqual(guard.update(11.6, 20), (True, 74))
        self.assertEqual(guard.update(11.8, 21), (False, None))

    def test_auto_mode_does_not_apply_12v_cutoff_to_24v_source(self):
        cfg = config()
        cfg["source_mode"] = "auto"
        guard = power.LowVoltageGuard(cfg)
        self.assertEqual(guard.update(24.0, 0), (False, None))
        self.assertEqual(guard.nominal, "24v")
        self.assertEqual(guard.update(24.0, 100), (False, None))

    def test_shutdown_uses_coordinated_dispatcher(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.object(power.subprocess, "run", return_value=completed) as run:
            power.request_coordinated_shutdown()
        run.assert_called_once_with(
            [power.SHUTDOWN_DISPATCHER, "shutdown-system"], check=False
        )

    def test_shutdown_falls_back_when_dispatcher_fails(self):
        failed = mock.Mock(returncode=1)
        completed = mock.Mock(returncode=0)
        with mock.patch.object(
            power.subprocess, "run", side_effect=[failed, completed]
        ) as run:
            power.request_coordinated_shutdown()
        self.assertEqual(
            run.call_args_list,
            [
                mock.call([power.SHUTDOWN_DISPATCHER, "shutdown-system"], check=False),
                mock.call(["systemctl", "poweroff"], check=False),
            ],
        )

    def test_dispatcher_enters_host_namespace_before_repository_check(self):
        source = (ROOT / "scripts" / "pcs-web-action.sh").read_text(encoding="utf-8")
        self.assertIn(
            "require_root\ndispatch_host_namespace_action\nensure_repo",
            source,
        )
        namespace_actions = source.split("dispatch_host_namespace_action()", 1)[1].split(
            "request_pistar_poweroff()", 1
        )[0]
        self.assertIn("shutdown-system", namespace_actions)


if __name__ == "__main__":
    unittest.main()
