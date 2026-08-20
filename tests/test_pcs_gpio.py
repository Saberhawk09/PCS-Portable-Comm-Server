import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pcs_gpio  # noqa: E402


STATS_SERVICE = ROOT / "systemd" / "pcs-gpio-stats.service"
STATS_SETUP = ROOT / "scripts" / "setup-gpio-stats.sh"


class PcsGpioTests(unittest.TestCase):
    def test_final_schematic_assignments_have_no_gpio_conflicts(self):
        gpio_lines = [pin.gpio for pin in pcs_gpio.PIN_ASSIGNMENTS]
        self.assertEqual(len(gpio_lines), len(set(gpio_lines)))
        expected = {
            "APRS PTT": (6, 31),
            "Fan PWM": (18, 12),
            "WS2812 data": (21, 40),
            "MAX7219 DIN": (10, 19),
            "MAX7219 CLK": (11, 23),
            "MAX7219 CS": (8, 24),
            "SA818 UART TX": (14, 8),
            "SA818 UART RX": (15, 10),
        }
        actual = {pin.function: (pin.gpio, pin.physical) for pin in pcs_gpio.PIN_ASSIGNMENTS}
        for function, assignment in expected.items():
            self.assertEqual(actual[function], assignment)

    def test_lcd_uses_the_six_schematic_gpio_lines(self):
        self.assertEqual(
            pcs_gpio.LCD_PINS,
            {"rs": 4, "enable": 17, "d4": 22, "d5": 23, "d6": 24, "d7": 25},
        )

    def test_max7219_uses_the_proven_pcs_spi_settings(self):
        self.assertEqual(pcs_gpio.MAX7219_SPI_BUS, 0)
        self.assertEqual(pcs_gpio.MAX7219_SPI_DEVICE, 0)
        self.assertEqual(pcs_gpio.MAX7219_SPI_HZ, 500_000)
        self.assertEqual(pcs_gpio.MAX7219_INTENSITY, 3)

    def test_temperature_reader_rounds_millidegrees(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "temp"
            path.write_text("38925\n", encoding="ascii")
            self.assertEqual(pcs_gpio.read_temperature(path), 39)

    def test_cellular_quality_parser(self):
        output = "modem.generic.signal-quality.value              : 12\n"
        self.assertEqual(pcs_gpio.parse_cellular_quality(output), 12)
        self.assertIsNone(pcs_gpio.parse_cellular_quality(""))

    def test_two_digit_renderer_clamps_and_handles_unknown(self):
        self.assertEqual(len(pcs_gpio.render_two_digits(39)), 8)
        self.assertEqual(pcs_gpio.render_two_digits(150), pcs_gpio.render_two_digits(99))
        self.assertNotEqual(pcs_gpio.render_two_digits(None), pcs_gpio.render_two_digits(0))

    def test_gps_sky_satellite_count_prefers_nsats_and_has_array_fallback(self):
        self.assertEqual(pcs_gpio.satellite_count_from_sky({"nSat": 9}), 9)
        self.assertEqual(pcs_gpio.satellite_count_from_sky({"nSat": 120}), 99)
        self.assertEqual(pcs_gpio.satellite_count_from_sky({"satellites": [{}, {}, {}]}), 3)
        self.assertIsNone(pcs_gpio.satellite_count_from_sky({}))

    def test_gps_status_keeps_fullest_sky_report_and_best_fix(self):
        status = (None, None)
        records = (
            {"class": "SKY", "nSat": 9, "uSat": 5},
            {"class": "TPV", "mode": 1},
            {"class": "SKY", "nSat": 21, "uSat": 14},
            {"class": "TPV", "mode": 3},
            {"class": "SKY", "nSat": 9, "uSat": 5},
            {"class": "TPV", "mode": 1},
        )
        for record in records:
            status = pcs_gpio.merge_gps_status(*status, record)
        self.assertEqual(status, (21, True))

    def test_one_stats_rotation_writes_only_matrix_frames(self):
        class FakeMatrix:
            def __init__(self):
                self.frames = []

            def rows(self, rows):
                self.frames.append(tuple(rows))

            def close(self):
                pass

        matrix = FakeMatrix()
        snapshot = pcs_gpio.StatsSnapshot(39, 12, 7, True)
        output = io.StringIO()
        with redirect_stdout(output):
            pcs_gpio.run_stats(
                matrix,
                once=True,
                collector=lambda: snapshot,
                sleeper=lambda _: None,
            )
        self.assertEqual(len(matrix.frames), 6)
        self.assertEqual(matrix.frames[0], pcs_gpio.DEGREE_C_ICON)
        self.assertEqual(matrix.frames[1], pcs_gpio.render_two_digits(39))
        self.assertEqual(matrix.frames[-2], pcs_gpio.SATELLITE_DISH_ICON)
        self.assertEqual(matrix.frames[-1], pcs_gpio.render_two_digits(7))
        self.assertEqual(json.loads(output.getvalue()), snapshot.as_dict())
        self.assertNotIn("field_clients", snapshot.as_dict())
        self.assertNotIn("usb_used_percent", snapshot.as_dict())

    def test_temperature_unit_frame_is_degree_c_not_thermometer(self):
        self.assertEqual(len(pcs_gpio.DEGREE_C_ICON), 8)
        self.assertEqual(pcs_gpio.DEGREE_C_ICON[:3], (0xE7, 0xA8, 0xE8))

    def test_gps_value_replaces_satellite_number_when_fix_is_unavailable(self):
        no_fix = pcs_gpio.stats_frames(pcs_gpio.StatsSnapshot(39, 12, 7, False))[-1]
        no_satellites = pcs_gpio.stats_frames(pcs_gpio.StatsSnapshot(39, 12, 0, True))[-1]
        unknown = pcs_gpio.stats_frames(pcs_gpio.StatsSnapshot(39, 12, None, None))[-1]
        self.assertEqual(no_fix.rows, pcs_gpio.NO_FIX_ICON)
        self.assertEqual(no_satellites.rows, pcs_gpio.NO_FIX_ICON)
        self.assertEqual(unknown.rows, pcs_gpio.UNKNOWN_ICON)

    def test_stats_service_is_spi_only_and_hardened(self):
        service = STATS_SERVICE.read_text(encoding="utf-8")
        setup = STATS_SETUP.read_text(encoding="utf-8")
        self.assertIn("pcs-gpio stats --hardware --apply", service)
        self.assertIn("DeviceAllow=/dev/spidev0.0 rw", service)
        self.assertIn("NoNewPrivileges=yes", service)
        self.assertNotIn("GPIO6", service)
        self.assertNotIn("PTT", service)
        self.assertIn("systemctl enable --now pcs-gpio-stats.service", setup)
        self.assertIn("raspi-config nonint do_spi 0", setup)
        self.assertIn("apt-get install -y python3-spidev", setup)

    def test_all_demo_excludes_fan_ptt_uart_and_rtc(self):
        backend = pcs_gpio.MockBackend()
        pcs_gpio.run_demo(backend, "all", duration=0, pause=lambda _: None)
        devices = [event["device"] for event in backend.events]
        self.assertEqual(devices, ["lcd", "matrix", "leds", "controller"])
        self.assertNotIn("fan", devices)
        self.assertNotIn("ptt", devices)
        self.assertNotIn("uart", devices)

    def test_fan_requires_explicit_duty(self):
        with self.assertRaisesRegex(ValueError, "explicit --fan-duty"):
            pcs_gpio.run_demo(pcs_gpio.MockBackend(), "fan", duration=0, pause=lambda _: None)

    def test_fan_duty_is_recorded_in_simulation(self):
        backend = pcs_gpio.MockBackend()
        pcs_gpio.run_demo(backend, "fan", duration=0, fan_duty=72.5, pause=lambda _: None)
        self.assertEqual(backend.events[0], {"device": "fan", "duty_percent": 72.5})

    def test_invalid_fan_duty_is_rejected_before_any_write(self):
        backend = pcs_gpio.MockBackend()
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            pcs_gpio.run_demo(backend, "fan", duration=0, fan_duty=101, pause=lambda _: None)
        self.assertEqual(backend.events, [])

    def test_pin_json_command_is_machine_readable(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = pcs_gpio.main(("pins", "--json"))
        self.assertEqual(result, 0)
        parsed = json.loads(output.getvalue())
        self.assertTrue(any(pin["function"] == "APRS PTT" and pin["gpio"] == 6 for pin in parsed))

    def test_real_demo_requires_double_confirmation(self):
        with self.assertRaisesRegex(SystemExit, "--hardware and --apply"):
            pcs_gpio.main(("demo", "lcd", "--hardware", "--duration", "0"))


if __name__ == "__main__":
    unittest.main()
