import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pcs_gpio  # noqa: E402


STATS_SERVICE = ROOT / "systemd" / "pcs-gpio-stats.service"
STATS_SETUP = ROOT / "scripts" / "setup-gpio-stats.sh"
LCD_SERVICE = ROOT / "systemd" / "pcs-gpio-lcd.service"
LCD_SETUP = ROOT / "scripts" / "setup-gpio-lcd.sh"
FAN_SERVICE = ROOT / "systemd" / "pcs-gpio-fan.service"
FAN_SETUP = ROOT / "scripts" / "setup-gpio-fan.sh"
LEDS_SERVICE = ROOT / "systemd" / "pcs-gpio-leds.service"
LEDS_SETUP = ROOT / "scripts" / "setup-gpio-leds.sh"
SHUTDOWN_SERVICE = ROOT / "systemd" / "pcs-gpio-shutdown.service"
STARTUP_SERVICE = ROOT / "systemd" / "pcs-gpio-startup.service"
STARTUP_SCRIPT = ROOT / "scripts" / "pcs-gpio-startup.sh"
PCS_STATUS = ROOT / "scripts" / "pcs-status.sh"
PCS_SELF_TEST = ROOT / "scripts" / "pcs-self-test.sh"


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
            "LCD D4": (27, 13),
            "LCD D5": (22, 15),
            "LCD D6": (23, 16),
            "LCD D7": (24, 18),
        }
        actual = {pin.function: (pin.gpio, pin.physical) for pin in pcs_gpio.PIN_ASSIGNMENTS}
        for function, assignment in expected.items():
            self.assertEqual(actual[function], assignment)

    def test_lcd_uses_the_six_schematic_gpio_lines(self):
        self.assertEqual(
            pcs_gpio.LCD_PINS,
            {"rs": 4, "enable": 17, "d4": 27, "d5": 22, "d6": 23, "d7": 24},
        )

    def test_lcd_line_normalization_is_exactly_two_rows_of_sixteen(self):
        self.assertEqual(
            pcs_gpio.normalize_lcd_lines(("PCS ONLINE", "A line that is much too long", "ignored")),
            ("   PCS ONLINE   ", "A line that is m"),
        )
        self.assertEqual(pcs_gpio.normalize_lcd_lines(("one\nline",)), ("    one line    ", "                "))
        self.assertEqual(pcs_gpio.HD44780_CHARACTER_CODES["°"], 0xDF)

    def test_max7219_uses_the_proven_pcs_spi_settings(self):
        self.assertEqual(pcs_gpio.MAX7219_SPI_BUS, 0)
        self.assertEqual(pcs_gpio.MAX7219_SPI_DEVICE, 0)
        self.assertEqual(pcs_gpio.MAX7219_SPI_HZ, 500_000)
        self.assertEqual(pcs_gpio.MAX7219_INTENSITY, 3)

    def test_fan_uses_gpio18_hardware_pwm_at_vendor_frequency(self):
        self.assertEqual(pcs_gpio.FAN_PWM_PIN, 18)
        self.assertEqual(pcs_gpio.FAN_PWM_CHANNEL, 0)
        self.assertEqual(pcs_gpio.FAN_PWM_FREQUENCY_HZ, 100)
        self.assertEqual(pcs_gpio.FAN_PWM_PERIOD_NS, 10_000_000)

    def test_ws2812_uses_six_dim_grb_pixels_on_gpio21_pcm(self):
        self.assertEqual(pcs_gpio.WS2812_PIN, 21)
        self.assertEqual(pcs_gpio.WS2812_COUNT, 6)
        self.assertEqual(pcs_gpio.WS2812_FREQUENCY_HZ, 800_000)
        self.assertEqual(pcs_gpio.WS2812_DMA_CHANNEL, 10)
        self.assertEqual(pcs_gpio.WS2812_BRIGHTNESS, 32)
        self.assertEqual(pcs_gpio.LED_WARNING, (220, 48, 0))
        self.assertEqual(pcs_gpio.LED_CRITICAL, (176, 0, 0))
        source = (ROOT / "scripts" / "pcs_gpio.py").read_text(encoding="utf-8")
        self.assertIn("ws.WS2811_STRIP_GRB", source)

    def test_shutdown_state_has_requested_lcd_blue_leds_and_bed_zzz_icon(self):
        self.assertEqual(pcs_gpio.SHUTDOWN_LCD_LINES, ("PCS Offline", "Shutting Down"))
        self.assertEqual(pcs_gpio.LED_SHUTDOWN, (0, 0, 255))
        self.assertEqual(
            pcs_gpio.SHUTDOWN_LED_COLORS,
            ((0, 0, 255),) * pcs_gpio.WS2812_COUNT,
        )
        self.assertEqual(
            pcs_gpio.BED_ZZZ_ICON,
            (0xDB, 0x49, 0xDB, 0x00, 0xC0, 0xFF, 0x81, 0x81),
        )
        self.assertEqual(pcs_gpio.SHUTDOWN_MATRIX_INTENSITY, 1)

    def test_startup_state_has_requested_message_spectrum_and_all_pixel_frame(self):
        self.assertEqual(pcs_gpio.STARTUP_LCD_LINES, ("PCS Booting Up", "Stand by..."))
        self.assertIn((255, 0, 0), pcs_gpio.STARTUP_LED_SEQUENCE)
        self.assertIn((0, 255, 0), pcs_gpio.STARTUP_LED_SEQUENCE)
        self.assertIn((0, 0, 255), pcs_gpio.STARTUP_LED_SEQUENCE)
        self.assertIn((255, 255, 255), pcs_gpio.STARTUP_LED_SEQUENCE)
        self.assertGreater(pcs_gpio.STARTUP_LED_FRAME_SECONDS, pcs_gpio.STARTUP_FRAME_SECONDS)
        self.assertEqual(pcs_gpio.STARTUP_MATRIX_FRAMES[0], (0xFF,) * 8)

    def test_startup_led_and_matrix_self_tests_latch_boot_frames(self):
        class FakeLeds:
            def __init__(self):
                self.frames = []

            def colors(self, colors):
                self.frames.append(tuple(colors))

        class FakeMatrix:
            def __init__(self):
                self.frames = []
                self.intensities = []

            def rows(self, rows):
                self.frames.append(tuple(rows))

            def intensity(self, value):
                self.intensities.append(value)

        leds = FakeLeds()
        matrix = FakeMatrix()
        pauses = []
        pcs_gpio.run_startup_leds(leds, sleeper=pauses.append)
        pcs_gpio.run_startup_matrix(matrix, sleeper=pauses.append)
        self.assertEqual(len(leds.frames), len(pcs_gpio.STARTUP_LED_SEQUENCE) + 1)
        self.assertTrue(all(len(frame) == pcs_gpio.WS2812_COUNT for frame in leds.frames))
        self.assertEqual(leds.frames[-1], (pcs_gpio.STARTUP_LED_LATCH,) * 6)
        self.assertEqual(matrix.frames[0], (0xFF,) * 8)
        self.assertEqual(matrix.frames[-1], pcs_gpio.STARTUP_MATRIX_LATCH)
        self.assertEqual(matrix.intensities, [pcs_gpio.STARTUP_MATRIX_INTENSITY, 1])

    def test_startup_led_repeat_restarts_the_spectrum(self):
        class StopAnimation(Exception):
            pass

        class FakeLeds:
            def __init__(self):
                self.frames = []

            def colors(self, colors):
                self.frames.append(tuple(colors))

        leds = FakeLeds()

        def stop_after_repeat(_seconds):
            if len(leds.frames) > len(pcs_gpio.STARTUP_LED_SEQUENCE):
                raise StopAnimation

        with self.assertRaises(StopAnimation):
            pcs_gpio.run_startup_leds(leds, repeat=True, sleeper=stop_after_repeat)
        self.assertEqual(
            leds.frames[len(pcs_gpio.STARTUP_LED_SEQUENCE)],
            (pcs_gpio.STARTUP_LED_SEQUENCE[0],) * pcs_gpio.WS2812_COUNT,
        )

    def test_startup_readiness_is_healthy_only_when_alerts_are_absent(self):
        healthy = pcs_gpio.MatrixHealthSnapshot(
            stats=pcs_gpio.StatsSnapshot(40, 20, 8, True, network_uplink="WiFi"),
            root_used_percent=20,
            primary_usb_mounted=True,
            failed_services=0,
            pistar_online=True,
            router_online=True,
        )
        warning = pcs_gpio.MatrixHealthSnapshot(
            stats=pcs_gpio.StatsSnapshot(40, 20, 0, False, network_uplink="Offline"),
            root_used_percent=20,
            primary_usb_mounted=True,
            failed_services=0,
            pistar_online=False,
            router_online=True,
        )
        self.assertTrue(pcs_gpio.startup_readiness(healthy)["ready"])
        self.assertFalse(pcs_gpio.startup_readiness(warning)["ready"])

    def test_shutdown_writes_are_latched_instead_of_cleared(self):
        class FakeLcd:
            def __init__(self):
                self.lines = None
                self.clear = None

            def text(self, lines):
                self.lines = tuple(lines)

            def close(self, *, clear=True):
                self.clear = clear

        class FakeLeds:
            def __init__(self):
                self.frame = None
                self.clear = None

            def colors(self, colors):
                self.frame = tuple(colors)

            def close(self, *, clear=True):
                self.clear = clear

        class FakeMatrix:
            def __init__(self):
                self.frame = None
                self.level = None
                self.clear = None

            def rows(self, rows):
                self.frame = tuple(rows)

            def intensity(self, value):
                self.level = value

            def close(self, *, clear=True):
                self.clear = clear

        lcd = FakeLcd()
        leds = FakeLeds()
        matrix = FakeMatrix()
        with (
            patch.object(pcs_gpio, "HD44780", return_value=lcd),
            patch.object(pcs_gpio, "Ws2812", return_value=leds),
            patch.object(pcs_gpio, "Max7219", return_value=matrix),
        ):
            pcs_gpio.apply_shutdown_state("lcd")
            pcs_gpio.apply_shutdown_state("leds")
            pcs_gpio.apply_shutdown_state("matrix")
        self.assertEqual(lcd.lines, pcs_gpio.SHUTDOWN_LCD_LINES)
        self.assertFalse(lcd.clear)
        self.assertEqual(leds.frame, pcs_gpio.SHUTDOWN_LED_COLORS)
        self.assertFalse(leds.clear)
        self.assertEqual(matrix.frame, pcs_gpio.BED_ZZZ_ICON)
        self.assertEqual(matrix.level, pcs_gpio.SHUTDOWN_MATRIX_INTENSITY)
        self.assertFalse(matrix.clear)

    def test_hardware_pwm_fan_initializes_and_closes_at_full_duty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            chip = Path(temp_dir) / "pwmchip0"
            channel = chip / "pwm0"
            channel.mkdir(parents=True)
            (channel / "enable").write_text("0\n", encoding="ascii")
            (channel / "period").write_text("0\n", encoding="ascii")
            (channel / "duty_cycle").write_text("0\n", encoding="ascii")
            fan = pcs_gpio.HardwarePwmFan(chip_path=chip)
            self.assertEqual((channel / "period").read_text(encoding="ascii"), "10000000\n")
            self.assertEqual((channel / "duty_cycle").read_text(encoding="ascii"), "10000000\n")
            self.assertEqual((channel / "enable").read_text(encoding="ascii"), "1\n")
            fan.duty(40)
            self.assertEqual((channel / "duty_cycle").read_text(encoding="ascii"), "4000000\n")
            fan.close()
            self.assertEqual((channel / "duty_cycle").read_text(encoding="ascii"), "10000000\n")

    def test_fan_curve_is_conservative_hysteretic_and_fail_safe(self):
        self.assertEqual(pcs_gpio.fan_duty_for_temperature(None), 100)
        self.assertEqual(pcs_gpio.fan_duty_for_temperature(39), 40)
        self.assertEqual(pcs_gpio.fan_duty_for_temperature(45), 55)
        self.assertEqual(pcs_gpio.fan_duty_for_temperature(55), 70)
        self.assertEqual(pcs_gpio.fan_duty_for_temperature(65), 85)
        self.assertEqual(pcs_gpio.fan_duty_for_temperature(75), 100)
        self.assertEqual(pcs_gpio.fan_duty_for_temperature(44, 55), 55)
        self.assertEqual(pcs_gpio.fan_duty_for_temperature(42, 55), 40)

    def test_temperature_reader_rounds_millidegrees(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "temp"
            path.write_text("38925\n", encoding="ascii")
            self.assertEqual(pcs_gpio.read_temperature(path), 39)

    def test_cellular_quality_parser(self):
        output = (
            "modem.generic.state                             : registered\n"
            "modem.generic.signal-quality.value              : 12\n"
        )
        self.assertEqual(pcs_gpio.parse_cellular_quality(output), 12)
        self.assertEqual(
            pcs_gpio.parse_cellular_quality("modem.generic.signal-quality.value : 100\n"),
            100,
        )
        self.assertTrue(pcs_gpio.parse_cellular_state(output))
        self.assertFalse(pcs_gpio.parse_cellular_state("modem.generic.state : disabled\n"))
        self.assertIsNone(pcs_gpio.parse_cellular_quality(""))

    def test_cellular_data_state_follows_networkmanager_not_modem_registration(self):
        self.assertTrue(pcs_gpio.parse_cellular_data_state("cdc-wdm0:gsm:connected\n"))
        self.assertFalse(pcs_gpio.parse_cellular_data_state("cdc-wdm0:gsm:disconnected\n"))
        self.assertIsNone(pcs_gpio.parse_cellular_data_state("wlan0:wifi:connected\n"))

    def test_network_uplink_parser_uses_pcs_interface_classes(self):
        self.assertEqual(pcs_gpio.parse_network_uplink("default dev wlan0"), "WiFi")
        self.assertEqual(pcs_gpio.parse_network_uplink("8.8.8.8 dev wwan0 src 10.0.0.2"), "Cellular")
        self.assertEqual(pcs_gpio.parse_network_uplink("8.8.8.8 dev ppp1"), "Cellular")
        self.assertEqual(pcs_gpio.parse_network_uplink(""), "Offline")

    def test_ap_client_count_excludes_infrastructure_and_inactive_neighbors(self):
        neighbors = "\n".join((
            "10.42.0.2 lladdr aa:aa:aa:aa:aa:02 STALE",
            "10.42.0.3 lladdr aa:aa:aa:aa:aa:03 REACHABLE",
            "10.42.0.105 lladdr aa:aa:aa:aa:aa:05 REACHABLE",
            "10.42.0.232 lladdr aa:aa:aa:aa:aa:32 DELAY",
            "10.42.0.150 FAILED",
        ))
        self.assertEqual(pcs_gpio.parse_ap_client_count(neighbors), 2)

    def test_pistar_probe_is_optional_and_uses_configured_reachability(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "pcs-install.conf"
            config.write_text("PCS_SETUP_PISTAR=no\n", encoding="utf-8")
            with patch("pcs_gpio.subprocess.run") as run:
                self.assertIsNone(pcs_gpio.read_pistar_online(config, "10.42.0.3"))
                run.assert_not_called()

            config.write_text("PCS_SETUP_PISTAR='yes'\n", encoding="utf-8")
            with patch("pcs_gpio.subprocess.run", return_value=Mock(returncode=0)):
                self.assertTrue(pcs_gpio.read_pistar_online(config, "10.42.0.3"))

            config.unlink()
            pair_dir = Path(temp_dir) / "pistar-shutdown"
            pair_dir.mkdir()
            with patch("pcs_gpio.subprocess.run", return_value=Mock(returncode=0)):
                self.assertTrue(
                    pcs_gpio.read_pistar_online(config, "10.42.0.3", pair_dir)
                )

            config.write_text("PCS_SETUP_PISTAR=yes\n", encoding="utf-8")
            with (
                patch("pcs_gpio.subprocess.run", return_value=Mock(returncode=1)),
                patch("pcs_gpio.socket.create_connection", side_effect=OSError),
            ):
                self.assertFalse(pcs_gpio.read_pistar_online(config, "10.42.0.3"))

    def test_openwrt_router_probe_uses_bounded_reachability(self):
        with patch("pcs_gpio.subprocess.run", return_value=Mock(returncode=0)):
            self.assertTrue(pcs_gpio.read_router_online("10.42.0.2"))

        with (
            patch("pcs_gpio.subprocess.run", return_value=Mock(returncode=1)),
            patch("pcs_gpio.socket.create_connection", side_effect=OSError),
        ):
            self.assertFalse(pcs_gpio.read_router_online("10.42.0.2"))

    def test_maidenhead_grid_matches_pcs_web_algorithm(self):
        self.assertEqual(pcs_gpio.maidenhead_grid(38.123456, -77.123456), "FM18kc")
        self.assertIsNone(pcs_gpio.maidenhead_grid(100, 0))

    def test_two_digit_renderer_clamps_and_handles_unknown(self):
        self.assertEqual(len(pcs_gpio.render_two_digits(39)), 8)
        self.assertEqual(pcs_gpio.render_two_digits(150), pcs_gpio.render_two_digits(99))
        self.assertNotEqual(pcs_gpio.render_two_digits(None), pcs_gpio.render_two_digits(0))

    def test_gps_sky_satellite_count_prefers_nsats_and_has_array_fallback(self):
        self.assertEqual(pcs_gpio.satellite_count_from_sky({"nSat": 9}), 9)
        self.assertEqual(pcs_gpio.satellite_count_from_sky({"nSat": 120}), 99)
        self.assertEqual(pcs_gpio.satellite_count_from_sky({"satellites": [{}, {}, {}]}), 3)
        self.assertIsNone(pcs_gpio.satellite_count_from_sky({}))
        self.assertEqual(pcs_gpio.satellite_counts_from_sky({"nSat": 21, "uSat": 14}), (21, 14))

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

        details = (None, None, None)
        for record in records:
            details = pcs_gpio.merge_gps_details(*details, record)
        self.assertEqual(details, (21, 14, True))

    def test_uptime_reader_and_formatter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "uptime"
            path.write_text("93784.44 123.0\n", encoding="ascii")
            self.assertEqual(pcs_gpio.read_uptime_seconds(path), 93784)
        self.assertEqual(pcs_gpio.format_uptime(93784), "Up: 1d 02h 03m")
        self.assertEqual(pcs_gpio.format_uptime(None), "Up: --d --h --m")

    def test_lcd_status_pages_are_concise_and_cover_unknown_gps(self):
        snapshot = pcs_gpio.StatsSnapshot(
            39, 12, 21, True, True, 14, "Cellular", 2, "EN91qs",
            "ok", 0, 0, 0, 0,
        )
        pages = pcs_gpio.lcd_status_pages(snapshot, 93784)
        self.assertEqual(pages, (
            ("PCS Online", "Up: 1d 02h 03m"),
            ("Pi CPU Temp", "39°C / 102°F"),
            ("Network Uplink", "Cellular"),
            ("Cell Data: On", "Signal: 012%"),
            ("GPS Status: Lock", "View 21 Used 14"),
            ("AP Clients: 2", "GridSq: EN91qs"),
            ("APRS Stats: Ok", "Pkt RX:0 Msgs:0"),
        ))
        unknown = pcs_gpio.lcd_status_pages(pcs_gpio.StatsSnapshot(None, None, None, None), None)
        self.assertEqual(unknown, (
            ("PCS Online", "Up: --d --h --m"),
            ("Pi CPU Temp", "--°C / --°F"),
            ("Network Uplink", "Offline"),
            ("Cell Data: Off", "Signal: 000%"),
            ("GPS Status: Err", "View -- Used --"),
            ("AP Clients: --", "GridSq: ------"),
            ("APRS Stats: Err", "Pkt:-- Msgs:--"),
        ))
        self.assertTrue(all(len(line) <= 16 for page in pages + unknown for line in page))

        no_fix = pcs_gpio.lcd_status_pages(pcs_gpio.StatsSnapshot(39, 12, 8, False, True, 0), 60)
        self.assertEqual(no_fix[-3], ("GPS Status: NoFx", "View 08 Used 00"))

    def test_lcd_aprs_page_reports_unread_mail_and_total_message_counter(self):
        snapshot = pcs_gpio.StatsSnapshot(
            39, 12, 21, True, True, 14, "WiFi", 1, "EN91qs",
            "ok", 123, 9, 4, 2,
        )
        self.assertEqual(
            pcs_gpio.lcd_status_pages(snapshot, 60)[-1],
            ("APRS Stats: MSG", "Pkt:123 Msgs:9"),
        )

    def test_aprs_status_reader_accepts_only_fresh_aggregate_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "status.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "collected_at_epoch": 1000,
                "status": "ok",
                "packets_received": 12,
                "messages_received": 3,
                "mailbox_total": 2,
                "mailbox_unread": 1,
                "mailbox_body": "must be ignored",
            }), encoding="utf-8")
            self.assertEqual(
                pcs_gpio.read_aprs_status(path, now=lambda: 1005),
                ("ok", 12, 3, 2, 1),
            )
            self.assertEqual(
                pcs_gpio.read_aprs_status(path, now=lambda: 1020),
                ("warn", None, None, None, None),
            )

    def test_lcd_warnings_append_explanation_pages(self):
        stats = pcs_gpio.StatsSnapshot(39, 12, 21, True, True, 14, "WiFi", 1, "EN91qs")
        health = pcs_gpio.MatrixHealthSnapshot(stats, 20, False, 0)
        pages = pcs_gpio.lcd_health_pages(health, 93784)
        self.assertEqual(pages[:7], pcs_gpio.lcd_status_pages(stats, 93784))
        self.assertEqual(pages[7:], (("WARNING", "USB NOT MOUNTED"),))

    def test_configured_offline_pistar_warns_on_all_gpio_displays(self):
        stats = pcs_gpio.StatsSnapshot(39, 12, 21, True, True, 14, "WiFi", 1, "EN91qs")
        health = pcs_gpio.MatrixHealthSnapshot(stats, 20, True, 0, False)

        alerts = pcs_gpio.matrix_alerts(health)
        self.assertEqual(
            [(alert.name, alert.severity, alert.icon) for alert in alerts],
            [("pistar", "warning", pcs_gpio.PISTAR_ICON)],
        )
        self.assertEqual(
            pcs_gpio.PISTAR_ICON,
            (0x66, 0x3C, 0x7E, 0xFF, 0xFF, 0x7E, 0x3C, 0x18),
        )
        self.assertNotEqual(pcs_gpio.PISTAR_ICON, pcs_gpio.SERVICE_ICON)
        self.assertEqual(
            pcs_gpio.lcd_health_pages(health, 93784)[-1],
            ("WARNING", "PI-STAR OFFLINE"),
        )
        service_led = pcs_gpio.led_status_indicators(health)[3]
        self.assertEqual(service_led.state, "dependency_warning")
        self.assertEqual(service_led.color, pcs_gpio.LED_WARNING)

        local_failure = pcs_gpio.MatrixHealthSnapshot(stats, 20, True, 2, False)
        failed_service_led = pcs_gpio.led_status_indicators(local_failure)[3]
        self.assertEqual(failed_service_led.state, "failed")
        self.assertEqual(failed_service_led.color, pcs_gpio.LED_CRITICAL)

    def test_offline_openwrt_router_faults_on_all_gpio_displays(self):
        stats = pcs_gpio.StatsSnapshot(39, 12, 21, True, True, 14, "WiFi", 1, "EN91qs")
        health = pcs_gpio.MatrixHealthSnapshot(stats, 20, True, 0, True, False)

        alerts = pcs_gpio.matrix_alerts(health)
        self.assertEqual(
            [(alert.name, alert.severity, alert.icon) for alert in alerts],
            [("router", "critical", pcs_gpio.ROUTER_ICON)],
        )
        self.assertEqual(
            pcs_gpio.ROUTER_ICON,
            (0x7E, 0x81, 0x81, 0x3C, 0x42, 0x42, 0x18, 0x18),
        )
        self.assertEqual(
            pcs_gpio.lcd_health_pages(health, 93784),
            (("HARD FAULT", "ROUTER OFFLINE"),),
        )
        network_led = pcs_gpio.led_status_indicators(health)[4]
        self.assertEqual(network_led.state, "router_offline")
        self.assertEqual(network_led.color, pcs_gpio.LED_CRITICAL)
        self.assertFalse(health.as_dict()["router_online"])

        self_test = (ROOT / "scripts" / "pcs-self-test.sh").read_text(encoding="utf-8")
        self.assertIn('fail "OpenWrt AP does not respond', self_test)
        self.assertNotIn('warn "OpenWrt AP does not respond', self_test)

    def test_lcd_hard_faults_replace_normal_status_pages(self):
        stats = pcs_gpio.StatsSnapshot(86, 12, 0, False, False, 0, "Offline", 0, None)
        health = pcs_gpio.MatrixHealthSnapshot(stats, 96, False, 2)
        pages = pcs_gpio.lcd_health_pages(health, 93784)
        self.assertEqual(pages, (
            ("HARD FAULT", "CPU TEMP: 86°C"),
            ("HARD FAULT", "ROOT DISK: 96%"),
            ("HARD FAULT", "SERVICE FAILURE"),
        ))
        self.assertNotIn(("PCS Online", "Up: 1d 02h 03m"), pages)
        self.assertTrue(all(len(line) <= 16 for page in pages for line in page))

    def test_one_lcd_status_rotation_writes_all_pages_when_healthy(self):
        class FakeLcd:
            def __init__(self):
                self.pages = []

            def text(self, lines):
                self.pages.append(tuple(lines))

            def close(self, *, clear=True):
                pass

        lcd = FakeLcd()
        stats = pcs_gpio.StatsSnapshot(39, 12, 21, True, True, 14, "WiFi", 1, "EN91qs")
        snapshot = pcs_gpio.MatrixHealthSnapshot(stats, 20, True, 0)
        output = io.StringIO()
        with redirect_stdout(output):
            pcs_gpio.run_lcd_status(
                lcd,
                once=True,
                collector=lambda: snapshot,
                uptime_reader=lambda: 93784,
                sleeper=lambda _: None,
            )
        self.assertEqual(lcd.pages, list(pcs_gpio.lcd_status_pages(stats, 93784)))
        self.assertEqual(json.loads(output.getvalue())["health"], snapshot.as_dict())

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

    def test_healthy_matrix_annunciator_uses_dim_checkmark_only(self):
        health = pcs_gpio.MatrixHealthSnapshot(
            pcs_gpio.StatsSnapshot(39, 12, 7, True, False, 5, "WiFi", 1, "EN91qs"),
            20,
            True,
            0,
        )
        alerts = pcs_gpio.matrix_alerts(health)
        frames = pcs_gpio.matrix_alert_frames(alerts)
        self.assertEqual(alerts, ())
        self.assertEqual([frame.rows for frame in frames], [pcs_gpio.CHECK_ICON])
        self.assertEqual([frame.intensity for frame in frames], [1])

    def test_unread_aprs_mailbox_prepends_letter_icon(self):
        frames = pcs_gpio.matrix_alert_frames((), mailbox_unread=2)
        self.assertEqual([frame.rows for frame in frames], [pcs_gpio.LETTER_ICON])
        self.assertEqual(frames[0].metric, "aprs_mailbox")
        self.assertEqual(frames[0].intensity, 5)

    def test_matrix_annunciator_prioritizes_critical_and_warning_conditions(self):
        self.assertEqual(
            pcs_gpio.EXCLAMATION_ICON,
            (0x18, 0x18, 0x18, 0x18, 0x18, 0x00, 0x18, 0x18),
        )
        health = pcs_gpio.MatrixHealthSnapshot(
            pcs_gpio.StatsSnapshot(86, 12, 0, False, False, 0, "Offline", 0, None),
            96,
            False,
            2,
        )
        alerts = pcs_gpio.matrix_alerts(health)
        self.assertEqual([alert.severity for alert in alerts[:3]], ["critical"] * 3)
        self.assertEqual({alert.name for alert in alerts}, {
            "cpu_temperature",
            "root_disk",
            "primary_usb",
            "failed_services",
            "network_uplink",
            "gps_fix",
        })
        frames = pcs_gpio.matrix_alert_frames(alerts)
        for index, alert in enumerate(alerts):
            prefix, subsystem = frames[index * 2:index * 2 + 2]
            expected_prefix = (
                pcs_gpio.X_ICON
                if alert.severity == "critical"
                else pcs_gpio.EXCLAMATION_ICON
            )
            expected_intensity = 10
            self.assertEqual(prefix.rows, expected_prefix)
            self.assertEqual(prefix.intensity, expected_intensity)
            self.assertEqual(subsystem.rows, alert.icon)
            self.assertEqual(subsystem.intensity, expected_intensity)

    def test_one_matrix_alert_rotation_writes_only_health_frames(self):
        class FakeMatrix:
            def __init__(self):
                self.frames = []
                self.intensities = []

            def intensity(self, value):
                self.intensities.append(value)

            def rows(self, rows):
                self.frames.append(tuple(rows))

            def close(self):
                pass

        matrix = FakeMatrix()
        health = pcs_gpio.MatrixHealthSnapshot(
            pcs_gpio.StatsSnapshot(39, 12, 7, True, False, 5, "WiFi", 1, "EN91qs"),
            20,
            True,
            0,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            pcs_gpio.run_matrix_alerts(
                matrix,
                once=True,
                collector=lambda: health,
                sleeper=lambda _: None,
            )
        self.assertEqual(matrix.frames, [pcs_gpio.CHECK_ICON])
        self.assertEqual(matrix.intensities, [1])
        self.assertEqual(json.loads(output.getvalue())["alerts"], [])

    def test_led_status_has_one_stable_responsibility_per_pixel(self):
        health = pcs_gpio.MatrixHealthSnapshot(
            pcs_gpio.StatsSnapshot(39, 12, 21, True, True, 14, "WiFi", 1, "EN91qs"),
            20,
            True,
            0,
        )
        indicators = pcs_gpio.led_status_indicators(health)
        self.assertEqual([indicator.pixel for indicator in indicators], list(range(6)))
        self.assertEqual([indicator.name for indicator in indicators], [
            "cpu_temperature",
            "root_disk",
            "primary_usb",
            "failed_services",
            "network_uplink",
            "gps_fix",
        ])
        self.assertEqual([indicator.state for indicator in indicators], [
            "healthy", "healthy", "mounted", "healthy", "wifi", "locked",
        ])
        self.assertEqual(indicators[4].color, pcs_gpio.LED_HEALTHY)

    def test_led_status_uses_fault_warning_and_unknown_colors(self):
        health = pcs_gpio.MatrixHealthSnapshot(
            pcs_gpio.StatsSnapshot(86, 12, None, None, False, None, "Offline", 0, None),
            90,
            False,
            2,
        )
        indicators = pcs_gpio.led_status_indicators(health)
        self.assertEqual([indicator.state for indicator in indicators], [
            "critical", "warning", "missing", "failed", "offline", "unknown",
        ])
        self.assertEqual(indicators[0].color, pcs_gpio.LED_CRITICAL)
        self.assertEqual(indicators[1].color, pcs_gpio.LED_WARNING)
        self.assertEqual(indicators[3].color, pcs_gpio.LED_CRITICAL)
        self.assertEqual(indicators[5].color, pcs_gpio.LED_UNKNOWN)

    def test_one_led_status_sample_writes_exactly_six_colors(self):
        class FakeLeds:
            def __init__(self):
                self.frames = []

            def colors(self, colors):
                self.frames.append(tuple(colors))

            def close(self):
                pass

        health = pcs_gpio.MatrixHealthSnapshot(
            pcs_gpio.StatsSnapshot(39, 12, 21, True, True, 14, "Cellular", 1, "EN91qs"),
            20,
            True,
            0,
        )
        leds = FakeLeds()
        output = io.StringIO()
        with redirect_stdout(output):
            pcs_gpio.run_led_status(
                leds,
                once=True,
                collector=lambda: health,
                sleeper=lambda _: None,
            )
        self.assertEqual(len(leds.frames), 1)
        self.assertEqual(len(leds.frames[0]), 6)
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed["health"], health.as_dict())
        self.assertEqual(parsed["indicators"][4]["state"], "cellular")

    def test_unread_aprs_mailbox_flashes_pixel_three_white_then_restores(self):
        class FakeLeds:
            def __init__(self):
                self.frames = []

            def colors(self, colors):
                self.frames.append(tuple(colors))

            def close(self):
                pass

        stats = pcs_gpio.StatsSnapshot(
            39, 12, 21, True, True, 14, "WiFi", 1, "EN91qs",
            "ok", 12, 3, 2, 1,
        )
        health = pcs_gpio.MatrixHealthSnapshot(stats, 20, True, 0)
        stable = tuple(indicator.color for indicator in pcs_gpio.led_status_indicators(health))
        leds = FakeLeds()
        sleeps = []
        with redirect_stdout(io.StringIO()):
            pcs_gpio.run_led_status(
                leds,
                once=True,
                collector=lambda: health,
                sleeper=sleeps.append,
            )
        self.assertEqual(len(leds.frames), 2)
        self.assertEqual(leds.frames[0][pcs_gpio.APRS_MESSAGE_PIXEL], pcs_gpio.LED_MESSAGE)
        self.assertEqual(leds.frames[1], stable)
        self.assertEqual(sleeps, [pcs_gpio.APRS_MESSAGE_FLASH_SECONDS])

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
        self.assertIn("pcs-gpio alerts --hardware --apply", service)
        self.assertIn("DeviceAllow=/dev/spidev0.0 rw", service)
        self.assertIn("NoNewPrivileges=yes", service)
        self.assertNotIn("GPIO6", service)
        self.assertNotIn("PTT", service)
        self.assertIn("systemctl enable --now pcs-gpio-stats.service", setup)
        self.assertIn("raspi-config nonint do_spi 0", setup)
        self.assertIn("apt-get install -y python3-spidev", setup)

    def test_lcd_service_is_gpio_only_and_hardened(self):
        service = LCD_SERVICE.read_text(encoding="utf-8")
        setup = LCD_SETUP.read_text(encoding="utf-8")
        self.assertIn("pcs-gpio lcd-status --hardware --apply", service)
        self.assertIn("DeviceAllow=/dev/gpiochip0 rw", service)
        self.assertIn("SupplementaryGroups=gpio", service)
        self.assertIn("RuntimeDirectory=pcs-gpio-lcd", service)
        self.assertIn("WorkingDirectory=/run/pcs-gpio-lcd", service)
        self.assertIn("NoNewPrivileges=yes", service)
        self.assertNotIn("spidev", service)
        self.assertNotIn("PTT", service)
        self.assertIn("systemctl enable --now pcs-gpio-lcd.service", setup)
        self.assertIn("apt-get install -y python3-gpiozero", setup)

    def test_fan_service_uses_hardware_pwm_and_full_duty_stop_failsafe(self):
        service = FAN_SERVICE.read_text(encoding="utf-8")
        setup = FAN_SETUP.read_text(encoding="utf-8")
        self.assertIn("pcs-gpio fan-control --hardware --apply", service)
        self.assertIn("pcs-gpio fan-failsafe --hardware --apply", service)
        self.assertIn("User=root", service)
        self.assertIn("NoNewPrivileges=yes", service)
        self.assertNotIn("ProtectKernelTunables=yes", service)
        self.assertIn("dtparam=audio=off", setup)
        self.assertIn("dtoverlay=pwm,pin=18,func=2", setup)
        self.assertIn("systemctl enable pcs-gpio-fan.service", setup)

    def test_led_service_uses_gpio21_pcm_and_is_reinstallable(self):
        service = LEDS_SERVICE.read_text(encoding="utf-8")
        setup = LEDS_SETUP.read_text(encoding="utf-8")
        self.assertIn("pcs-gpio led-status --hardware --apply", service)
        self.assertIn("User=root", service)
        self.assertIn("DeviceAllow=/dev/mem rw", service)
        self.assertIn("NoNewPrivileges=yes", service)
        self.assertIn("rpi-ws281x==${WS281X_VERSION}", setup)
        self.assertIn("python3 -m venv", setup)
        self.assertIn("systemctl enable --now pcs-gpio-leds.service", setup)
        self.assertNotIn("dtparam=audio=off", setup)
        self.assertNotIn("GPIO6", service)
        self.assertNotIn("PTT", service)

    def test_shutdown_service_runs_after_normal_display_daemons_stop(self):
        service = SHUTDOWN_SERVICE.read_text(encoding="utf-8")
        self.assertIn("DefaultDependencies=no", service)
        self.assertIn("Conflicts=shutdown.target", service)
        self.assertIn(
            "Before=pcs-gpio-startup.service pcs-gpio-lcd.service pcs-gpio-leds.service pcs-gpio-stats.service shutdown.target",
            service,
        )
        self.assertIn("RemainAfterExit=yes", service)
        self.assertIn("shutdown-state lcd --hardware --apply", service)
        self.assertIn("shutdown-state leds --hardware --apply", service)
        self.assertIn("shutdown-state matrix --hardware --apply", service)
        self.assertIn("DeviceAllow=/dev/gpiochip0 rw", service)
        self.assertIn("DeviceAllow=/dev/spidev0.0 rw", service)
        self.assertIn("DeviceAllow=/dev/mem rw", service)

    def test_startup_service_is_bounded_hardened_and_orders_normal_displays(self):
        service = STARTUP_SERVICE.read_text(encoding="utf-8")
        script = STARTUP_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Before=pcs-gpio-lcd.service pcs-gpio-leds.service pcs-gpio-stats.service", service)
        self.assertIn("After=local-fs.target pcs-gpio-shutdown.service", service)
        self.assertIn("TimeoutStartSec=150", service)
        self.assertIn("RemainAfterExit=yes", service)
        self.assertIn("DeviceAllow=/dev/gpiochip0 rw", service)
        self.assertIn("DeviceAllow=/dev/spidev0.0 rw", service)
        self.assertIn("DeviceAllow=/dev/mem rw", service)
        self.assertIn('TIMEOUT_SECONDS="${PCS_GPIO_STARTUP_TIMEOUT_SECONDS:-90}"', script)
        self.assertIn("startup-state lcd --hardware --apply", script)
        self.assertIn("startup-state leds --repeat --hardware --apply", script)
        self.assertIn("startup-state matrix --hardware --apply", script)
        self.assertIn("trap on_exit EXIT", script)
        self.assertIn('kill "${led_animation_pid}"', script)
        self.assertIn('wait "${led_animation_pid}"', script)
        self.assertIn('"${DRIVER}" startup-ready', script)
        self.assertIn("persistent alerts remain visible", script)

    def test_normal_indicator_services_wait_for_startup_handoff(self):
        for path in (LCD_SERVICE, LEDS_SERVICE, STATS_SERVICE):
            service = path.read_text(encoding="utf-8")
            with self.subTest(service=path.name):
                self.assertIn("After=", service)
                self.assertIn("pcs-gpio-startup.service", service)
                self.assertIn("Wants=", service)

    def test_display_installers_install_and_enable_startup_service(self):
        for path in (LCD_SETUP, LEDS_SETUP, STATS_SETUP):
            setup = path.read_text(encoding="utf-8")
            with self.subTest(setup=path.name):
                self.assertIn("pcs-gpio-startup.sh", setup)
                self.assertIn("pcs-gpio-startup.service", setup)
                self.assertIn("systemctl enable pcs-gpio-startup.service", setup)

    def test_every_display_installer_registers_and_arms_shutdown_state(self):
        expected_markers = {
            LCD_SETUP: "SHUTDOWN_MARKER=\"${SHUTDOWN_MARKER_DIR}/lcd\"",
            LEDS_SETUP: "SHUTDOWN_MARKER=\"${SHUTDOWN_MARKER_DIR}/leds\"",
            STATS_SETUP: "SHUTDOWN_MARKER=\"${SHUTDOWN_MARKER_DIR}/matrix\"",
        }
        for setup_path, marker in expected_markers.items():
            setup = setup_path.read_text(encoding="utf-8")
            with self.subTest(setup=setup_path.name):
                self.assertIn("pcs-gpio-shutdown.service", setup)
                self.assertIn(marker, setup)
                self.assertIn("systemctl enable --now pcs-gpio-shutdown.service", setup)

    def test_shutdown_state_is_simulated_and_marker_aware(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker_dir = Path(temp_dir)
            with patch.object(pcs_gpio, "GPIO_SHUTDOWN_MARKER_DIR", marker_dir):
                output = io.StringIO()
                with redirect_stdout(output):
                    result = pcs_gpio.main(("shutdown-state", "lcd"))
                parsed = json.loads(output.getvalue())
                self.assertEqual(result, 0)
                self.assertEqual(parsed["lines"], ["  PCS Offline   ", " Shutting Down  "])
                self.assertFalse(parsed["registered"])
                self.assertFalse(parsed["writes_performed"])
                (marker_dir / "lcd").touch()
                self.assertTrue(pcs_gpio.shutdown_state_plan("lcd")["registered"])

    def test_startup_state_is_simulated_and_marker_aware(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker_dir = Path(temp_dir)
            with patch.object(pcs_gpio, "GPIO_SHUTDOWN_MARKER_DIR", marker_dir):
                output = io.StringIO()
                with redirect_stdout(output):
                    result = pcs_gpio.main(("startup-state", "lcd"))
                parsed = json.loads(output.getvalue())
                self.assertEqual(result, 0)
                self.assertEqual(parsed["lines"], [" PCS Booting Up ", "  Stand by...   "])
                self.assertFalse(parsed["registered"])
                self.assertFalse(parsed["writes_performed"])
                (marker_dir / "lcd").touch()
                self.assertTrue(pcs_gpio.startup_state_plan("lcd")["registered"])

    def test_real_shutdown_state_requires_double_confirmation(self):
        with self.assertRaisesRegex(SystemExit, "--hardware and --apply"):
            pcs_gpio.main(("shutdown-state", "leds", "--hardware"))

    def test_status_and_self_test_cover_every_gpio_daemon(self):
        status = PCS_STATUS.read_text(encoding="utf-8")
        self_test = PCS_SELF_TEST.read_text(encoding="utf-8")
        expected = {
            "PCS_SETUP_GPIO_LCD": "pcs-gpio-lcd.service",
            "PCS_SETUP_GPIO_LEDS": "pcs-gpio-leds.service",
            "PCS_SETUP_GPIO_STATS": "pcs-gpio-stats.service",
            "PCS_SETUP_GPIO_FAN": "pcs-gpio-fan.service",
        }
        for setting, service in expected.items():
            with self.subTest(script="pcs-status.sh", setting=setting):
                self.assertIn(setting, status)
                self.assertIn(service, status)
            with self.subTest(script="pcs-self-test.sh", setting=setting):
                self.assertIn(setting, self_test)
                self.assertIn(service, self_test)
        self.assertIn("pcs-gpio-shutdown.service", status)
        self.assertIn("pcs-gpio-shutdown.service", self_test)
        self.assertIn("pcs-gpio-startup.service", status)
        self.assertIn("pcs-gpio-startup.service", self_test)
        self.assertIn("/usr/local/sbin/pcs-gpio-startup", self_test)

    def test_dependency_report_accepts_isolated_ws2812_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            isolated_python = Path(temp_dir) / "python"
            isolated_python.touch()
            completed = Mock(returncode=0)
            with (
                patch("pcs_gpio.importlib.util.find_spec", return_value=None),
                patch("pcs_gpio.subprocess.run", return_value=completed) as run,
            ):
                self.assertTrue(
                    pcs_gpio.python_module_available("rpi_ws281x", isolated_python)
                )
            run.assert_called_once()

    def test_fan_control_is_simulated_by_default(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = pcs_gpio.main(("fan-control", "--once"))
        self.assertEqual(result, 0)
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed["frequency_hz"], 100)
        self.assertEqual(parsed["failsafe_duty_percent"], 100)
        self.assertFalse(parsed["writes_performed"])

    def test_real_fan_control_requires_double_confirmation(self):
        with self.assertRaisesRegex(SystemExit, "--hardware and --apply"):
            pcs_gpio.main(("fan-control", "--hardware", "--once"))

    def test_led_status_is_simulated_by_default(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = pcs_gpio.main(("led-status", "--once"))
        self.assertEqual(result, 0)
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed["gpio"], 21)
        self.assertEqual(parsed["pixel_count"], 6)
        self.assertFalse(parsed["writes_performed"])

    def test_real_led_status_requires_double_confirmation(self):
        with self.assertRaisesRegex(SystemExit, "--hardware and --apply"):
            pcs_gpio.main(("led-status", "--hardware", "--once"))

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

    def test_lcd_command_is_simulated_by_default(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = pcs_gpio.main(("lcd", "--line1", "PCS ONLINE", "--line2", "READY"))
        self.assertEqual(result, 0)
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed["lines"], ["   PCS ONLINE   ", "     READY      "])
        self.assertFalse(parsed["writes_performed"])

    def test_real_lcd_requires_double_confirmation(self):
        with self.assertRaisesRegex(SystemExit, "--hardware and --apply"):
            pcs_gpio.main(("lcd", "--hardware"))

    def test_lcd_status_is_simulated_by_default(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = pcs_gpio.main(("lcd-status", "--once", "--page-seconds", "0"))
        self.assertEqual(result, 0)
        parsed = json.loads(output.getvalue())
        self.assertGreaterEqual(len(parsed["pages"]), 1)
        self.assertIn("health", parsed)
        self.assertIn("alerts", parsed)
        self.assertFalse(parsed["writes_performed"])

    def test_real_lcd_status_requires_double_confirmation(self):
        with self.assertRaisesRegex(SystemExit, "--hardware and --apply"):
            pcs_gpio.main(("lcd-status", "--hardware", "--once"))

    def test_matrix_alerts_are_simulated_by_default(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = pcs_gpio.main(("alerts", "--once", "--frame-seconds", "0"))
        self.assertEqual(result, 0)
        parsed = json.loads(output.getvalue())
        self.assertFalse(parsed["writes_performed"])

    def test_real_matrix_alerts_require_double_confirmation(self):
        with self.assertRaisesRegex(SystemExit, "--hardware and --apply"):
            pcs_gpio.main(("alerts", "--hardware", "--once"))


if __name__ == "__main__":
    unittest.main()
