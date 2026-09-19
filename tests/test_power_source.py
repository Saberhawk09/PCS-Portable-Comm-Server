import json
import tempfile
import unittest
from pathlib import Path

from test_pcs_power_monitor import config, power
from test_aprs_agent import pcs_aprs_agent


class SourceTests(unittest.TestCase):
    def test_boot_and_corruption_fail_safe_and_same_boot_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            settings = {"dc_source": "power_supply", "battery_capacity_wh": 500}
            power.atomic_json(path, {**settings, "boot_id": "one"})
            self.assertEqual(power.read_source_settings(path, "one"), settings)
            self.assertEqual(power.read_source_settings(path, "two")["dc_source"], "battery")
            path.write_text('[]')
            self.assertEqual(power.read_source_settings(path, "one")["dc_source"], "battery")

    def test_supply_cancels_countdown_even_with_missing_sensor_then_battery_rearms(self):
        guard = power.LowVoltageGuard(config())
        for now in range(3):
            guard.update(11, now)
        self.assertTrue(guard.update(11, 30)[0])
        guard.dc_source = "power_supply"
        self.assertEqual(guard.update(None, 200), (False, None))
        guard.dc_source = "battery"
        self.assertEqual(guard.update(11, 201), (False, None))
        guard.update(11, 202)
        self.assertEqual(guard.update(11, 203), (True, 90))

    def test_capacity_uses_prior_energy_and_supply_keeps_branch_separate(self):
        monitors = {
            "input": dict(online=True, power=20, energy_since_boot_wh=6, charge_since_boot_mah=500),
            "starlink": dict(online=True, power=30, energy_since_boot_wh=2, charge_since_boot_mah=170),
            "rail_5v": dict(online=True, power=10, energy_since_boot_wh=3, charge_since_boot_mah=600),
        }
        battery = power.aggregate_power(monitors, dict(dc_source="battery", battery_capacity_wh=500))
        self.assertEqual((battery["power"], battery["charge_since_boot_mah"], battery["battery_remaining_wh"]), (50, 670, 492))
        supply = power.aggregate_power(monitors, dict(dc_source="power_supply", battery_capacity_wh=500))
        self.assertEqual((supply["power"], supply["energy_since_boot_wh"]), (20, 6))
        self.assertIsNone(supply["battery_remaining_wh"])
        self.assertEqual(monitors["starlink"]["energy_since_boot_wh"], 2)
        monitors["starlink"]["online"] = False
        self.assertIsNone(power.aggregate_power(monitors, dict(dc_source="battery"))["power"])

    def test_capacity_validation(self):
        for value in (True, -1, 0, float('nan'), float('inf'), '500'):
            with self.assertRaises(ValueError):
                power.source_settings(dict(dc_source="battery", battery_capacity_wh=value))


class UplinkTests(unittest.TestCase):
    def test_actual_active_route_and_fresh_starlink_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            uplink, dish = Path(directory) / "uplink.json", Path(directory) / "dish.json"
            provider = pcs_aprs_agent.StatusProvider(uplink_status_path=uplink, starlink_status_path=dish, wall_time=lambda: 100)
            payload = dict(generated_at=100, internet=True, uplinks=[
                dict(id="wifi", type="wifi", active=False, selected=True),
                dict(id="mini", type="ethernet", active=True),
            ])
            uplink.write_text(json.dumps(payload))
            self.assertEqual(provider.uplink_value(), "Ethernet WAN")
            dish.write_text(json.dumps(dict(collected_at=100, poll_seconds=15, configured=True, available=True, uplink_id="mini")))
            self.assertEqual(provider.uplink_value(), "Starlink")
            self.assertEqual(provider.network(), "NET Starlink")
            payload["generated_at"] = 0
            uplink.write_text(json.dumps(payload))
            self.assertEqual(provider.uplink_value(), "Unknown")

    def test_aprs_uses_aggregate_without_replacing_dc_input_voltage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "power.json"
            path.write_text(json.dumps(dict(version=1, collected_at_epoch=100,
                monitors=dict(input=dict(online=True, voltage=12, charge_since_boot_mah=500, energy_since_boot_wh=6)),
                aggregate=dict(charge_since_boot_mah=670, energy_since_boot_wh=8))))
            provider = pcs_aprs_agent.StatusProvider(power_status_path=path, wall_time=lambda: 100)
            self.assertEqual(provider.power(), "DC IN - 12v | Total PWR - 670mAh / 8Wh")
