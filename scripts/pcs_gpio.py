#!/usr/bin/env python3
"""PCS GPIO allocation and guarded device commissioning utility.

The default demo backend is a simulator. Real GPIO writes require both
``--hardware`` and ``--apply`` so importing or inspecting this module cannot
key the APRS radio or unexpectedly animate attached devices.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import signal
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence


@dataclass(frozen=True)
class PinAssignment:
    function: str
    gpio: int
    physical: int
    owner: str
    status: str


PIN_ASSIGNMENTS: tuple[PinAssignment, ...] = (
    PinAssignment("RTC SDA", 2, 3, "Linux I2C", "existing; kernel-managed"),
    PinAssignment("RTC SCL", 3, 5, "Linux I2C", "existing; kernel-managed"),
    PinAssignment("LCD RS", 4, 7, "pcs_gpio", "installed and bench-tested"),
    PinAssignment("APRS PTT", 6, 31, "Dire Wolf", "selected; activation-gated"),
    PinAssignment("MAX7219 CS", 8, 24, "Linux SPI0", "installed and bench-tested"),
    PinAssignment("MAX7219 DIN", 10, 19, "Linux SPI0", "installed and bench-tested"),
    PinAssignment("MAX7219 CLK", 11, 23, "Linux SPI0", "installed and bench-tested"),
    PinAssignment("SA818 UART TX", 14, 8, "Linux UART", "reserved; logic level unverified"),
    PinAssignment("SA818 UART RX", 15, 10, "Linux UART", "reserved; logic level unverified"),
    PinAssignment("LCD E", 17, 11, "pcs_gpio", "installed and bench-tested"),
    PinAssignment("Fan PWM", 18, 12, "pcs_gpio", "selected; behavior not measured"),
    PinAssignment("WS2812 data", 21, 40, "pcs_gpio", "selected; not bench-tested"),
    PinAssignment("LCD D4", 27, 13, "pcs_gpio", "installed and bench-tested"),
    PinAssignment("LCD D5", 22, 15, "pcs_gpio", "installed and bench-tested"),
    PinAssignment("LCD D6", 23, 16, "pcs_gpio", "installed and bench-tested"),
    PinAssignment("LCD D7", 24, 18, "pcs_gpio", "installed and bench-tested"),
)

LCD_PINS = {"rs": 4, "enable": 17, "d4": 27, "d5": 22, "d6": 23, "d7": 24}
LCD_COLUMNS = 16
LCD_ROWS = 2
HD44780_CHARACTER_CODES = {"°": 0xDF}
WS2812_PIN = 21
WS2812_COUNT = 6
FAN_PWM_PIN = 18
MAX7219_SPI_BUS = 0
MAX7219_SPI_DEVICE = 0
MAX7219_SPI_HZ = 500_000
MAX7219_INTENSITY = 3


class Backend(Protocol):
    def lcd(self, lines: Sequence[str]) -> None: ...
    def matrix(self, rows: Sequence[int]) -> None: ...
    def leds(self, colors: Sequence[tuple[int, int, int]]) -> None: ...
    def fan(self, duty_percent: float) -> None: ...
    def close(self) -> None: ...


class MockBackend:
    """Records intended writes without accessing GPIO hardware."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def lcd(self, lines: Sequence[str]) -> None:
        self.events.append({"device": "lcd", "lines": list(lines)})

    def matrix(self, rows: Sequence[int]) -> None:
        self.events.append({"device": "matrix", "rows": list(rows)})

    def leds(self, colors: Sequence[tuple[int, int, int]]) -> None:
        self.events.append({"device": "leds", "colors": [list(color) for color in colors]})

    def fan(self, duty_percent: float) -> None:
        self.events.append({"device": "fan", "duty_percent": duty_percent})

    def close(self) -> None:
        self.events.append({"device": "controller", "action": "close"})


class HD44780:
    """Minimal write-only HD44780 4-bit driver using GPIO Zero outputs."""

    def __init__(self) -> None:
        from gpiozero import DigitalOutputDevice

        self.rs = DigitalOutputDevice(LCD_PINS["rs"], initial_value=False)
        self.enable = DigitalOutputDevice(LCD_PINS["enable"], initial_value=False)
        self.data = [
            DigitalOutputDevice(LCD_PINS[name], initial_value=False)
            for name in ("d4", "d5", "d6", "d7")
        ]
        time.sleep(0.05)
        for nibble, pause in ((0x3, 0.005), (0x3, 0.001), (0x3, 0.001), (0x2, 0.001)):
            self._write_nibble(nibble)
            time.sleep(pause)
        for command in (0x28, 0x08, 0x01, 0x06, 0x0C):
            self.command(command)

    def _pulse(self) -> None:
        self.enable.on()
        time.sleep(0.000001)
        self.enable.off()
        time.sleep(0.00005)

    def _write_nibble(self, value: int) -> None:
        for bit, output in enumerate(self.data):
            output.value = bool(value & (1 << bit))
        self._pulse()

    def _send(self, value: int, *, data: bool) -> None:
        self.rs.value = data
        self._write_nibble((value >> 4) & 0x0F)
        self._write_nibble(value & 0x0F)
        if not data and value in (0x01, 0x02):
            time.sleep(0.002)

    def command(self, value: int) -> None:
        self._send(value, data=False)

    def text(self, lines: Sequence[str]) -> None:
        padded = normalize_lcd_lines(lines)
        for address, line in zip((0x00, 0x40), padded):
            self.command(0x80 | address)
            for character in line:
                code = HD44780_CHARACTER_CODES.get(character, ord(character))
                self._send(code if 0 <= code <= 0xFF else ord("?"), data=True)

    def close(self, *, clear: bool = True) -> None:
        if clear:
            self.command(0x01)
        for output in (*self.data, self.enable, self.rs):
            output.off()
        for output in (*self.data, self.enable, self.rs):
            output.close()


class Max7219:
    def __init__(self) -> None:
        import spidev

        self.spi = spidev.SpiDev()
        self.spi.open(MAX7219_SPI_BUS, MAX7219_SPI_DEVICE)
        self.spi.max_speed_hz = MAX7219_SPI_HZ
        self.spi.mode = 0
        for register, value in (
            (0x0F, 0),
            (0x09, 0),
            (0x0B, 7),
            (0x0A, MAX7219_INTENSITY),
            (0x0C, 1),
        ):
            self._write(register, value)
        self.rows([0] * 8)

    def _write(self, register: int, value: int) -> None:
        self.spi.xfer2([register & 0x0F, value & 0xFF])

    def rows(self, rows: Sequence[int]) -> None:
        if len(rows) != 8 or any(not 0 <= row <= 0xFF for row in rows):
            raise ValueError("MAX7219 rows must contain exactly eight byte values")
        for register, value in enumerate(rows, start=1):
            self._write(register, value)

    def close(self) -> None:
        self.rows([0] * 8)
        self._write(0x0C, 0)
        self.spi.close()


class Ws2812:
    def __init__(self) -> None:
        from rpi_ws281x import Color, PixelStrip

        self._color = Color
        # GPIO21 selects the PCM output path, leaving GPIO18 available for the
        # cooler's independent PWM signal. Brightness is intentionally low.
        self.strip = PixelStrip(WS2812_COUNT, WS2812_PIN, 800_000, 10, False, 32, 0)
        self.strip.begin()

    def colors(self, colors: Sequence[tuple[int, int, int]]) -> None:
        if len(colors) != WS2812_COUNT:
            raise ValueError(f"expected {WS2812_COUNT} WS2812 colors")
        for index, (red, green, blue) in enumerate(colors):
            if any(not 0 <= component <= 255 for component in (red, green, blue)):
                raise ValueError("WS2812 color components must be from 0 to 255")
            self.strip.setPixelColor(index, self._color(red, green, blue))
        self.strip.show()

    def close(self) -> None:
        self.colors([(0, 0, 0)] * WS2812_COUNT)


class FanPwm:
    def __init__(self) -> None:
        from gpiozero import PWMOutputDevice

        # The commissioning command begins at full duty. Automatic thermal
        # control waits for measured fan behavior and a reviewed fan curve.
        self.output = PWMOutputDevice(FAN_PWM_PIN, frequency=25_000, initial_value=1.0)

    def duty(self, duty_percent: float) -> None:
        if not 0 <= duty_percent <= 100:
            raise ValueError("fan duty must be between 0 and 100 percent")
        self.output.value = duty_percent / 100.0

    def close(self) -> None:
        self.output.value = 1.0
        self.output.close()


class RaspberryPiBackend:
    """Creates only the device drivers selected by a guarded demo."""

    def __init__(self) -> None:
        self._devices: list[object] = []

    def lcd(self, lines: Sequence[str]) -> None:
        device = HD44780()
        self._devices.append(device)
        device.text(lines)

    def matrix(self, rows: Sequence[int]) -> None:
        device = Max7219()
        self._devices.append(device)
        device.rows(rows)

    def leds(self, colors: Sequence[tuple[int, int, int]]) -> None:
        device = Ws2812()
        self._devices.append(device)
        device.colors(colors)

    def fan(self, duty_percent: float) -> None:
        device = FanPwm()
        self._devices.append(device)
        device.duty(duty_percent)

    def close(self) -> None:
        for device in reversed(self._devices):
            try:
                device.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._devices.clear()


CHECKERBOARD = (0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55)
LED_TEST_COLORS = ((32, 0, 0), (0, 32, 0), (0, 0, 32), (32, 16, 0), (0, 16, 32), (16, 0, 32))
SAFE_ALL_TARGETS = ("lcd", "matrix", "leds")

DIGITS: dict[str, tuple[int, ...]] = {
    "0": (0b111, 0b101, 0b101, 0b101, 0b111),
    "1": (0b010, 0b110, 0b010, 0b010, 0b111),
    "2": (0b111, 0b001, 0b111, 0b100, 0b111),
    "3": (0b111, 0b001, 0b111, 0b001, 0b111),
    "4": (0b101, 0b101, 0b111, 0b001, 0b001),
    "5": (0b111, 0b100, 0b111, 0b001, 0b111),
    "6": (0b111, 0b100, 0b111, 0b101, 0b111),
    "7": (0b111, 0b001, 0b010, 0b010, 0b010),
    "8": (0b111, 0b101, 0b111, 0b101, 0b111),
    "9": (0b111, 0b101, 0b111, 0b001, 0b111),
}

DEGREE_C_ICON = (0xE7, 0xA8, 0xE8, 0x08, 0x08, 0x08, 0x07, 0x00)
SIGNAL_ICON = (0x00, 0x00, 0x02, 0x02, 0x0A, 0x0A, 0x2A, 0xAA)
SATELLITE_DISH_ICON = (0x01, 0x05, 0x12, 0x0A, 0x3C, 0x18, 0x18, 0x3C)
NO_FIX_ICON = (0x81, 0x42, 0x24, 0x18, 0x18, 0x24, 0x42, 0x81)
UNKNOWN_ICON = (0x3C, 0x42, 0x02, 0x0C, 0x10, 0x00, 0x10, 0x00)


@dataclass(frozen=True)
class StatsSnapshot:
    temperature_c: int | None
    cellular_quality: int | None
    gps_satellites: int | None
    gps_locked: bool | None
    cellular_online: bool | None = None
    gps_satellites_used: int | None = None
    network_uplink: str | None = None
    ap_clients: int | None = None
    grid_square: str | None = None

    def as_dict(self) -> dict[str, int | bool | str | None]:
        return asdict(self)


class MatrixDisplay(Protocol):
    def rows(self, rows: Sequence[int]) -> None: ...
    def close(self) -> None: ...


class LcdDisplay(Protocol):
    def text(self, lines: Sequence[str]) -> None: ...

    def close(self, *, clear: bool = True) -> None: ...


@dataclass(frozen=True)
class StatsFrame:
    metric: str
    kind: str
    rows: tuple[int, ...]

    def as_dict(self) -> dict[str, str | list[int]]:
        return {"metric": self.metric, "kind": self.kind, "rows": list(self.rows)}


def selected_targets(target: str) -> tuple[str, ...]:
    return SAFE_ALL_TARGETS if target == "all" else (target,)


def normalize_lcd_lines(lines: Sequence[str]) -> tuple[str, str]:
    """Return exactly two printable 16-character HD44780 rows."""
    normalized = [str(line).replace("\n", " ").replace("\r", " ") for line in lines[:LCD_ROWS]]
    normalized.extend("" for _ in range(LCD_ROWS - len(normalized)))
    return tuple(line[:LCD_COLUMNS].ljust(LCD_COLUMNS) for line in normalized)  # type: ignore[return-value]


def run_demo(
    backend: Backend,
    target: str,
    *,
    duration: float = 3.0,
    fan_duty: float | None = None,
    pause: Callable[[float], None] = time.sleep,
) -> None:
    targets = selected_targets(target)
    if "fan" in targets and fan_duty is None:
        raise ValueError("the fan demo requires an explicit --fan-duty value")
    if "fan" in targets and not 0 <= float(fan_duty) <= 100:
        raise ValueError("fan duty must be between 0 and 100 percent")
    try:
        if "lcd" in targets:
            backend.lcd(("PCS GPIO TEST", "LCD: OK"))
        if "matrix" in targets:
            backend.matrix(CHECKERBOARD)
        if "leds" in targets:
            backend.leds(LED_TEST_COLORS)
        if "fan" in targets:
            backend.fan(float(fan_duty))
        pause(max(0.0, duration))
    finally:
        backend.close()


def render_two_digits(value: int | None) -> tuple[int, ...]:
    """Render a zero-padded 0-99 value in two centered 3x5 glyphs."""
    if value is None:
        rows = [0] * 8
        rows[3] = 0b11101110
        return tuple(rows)
    text = f"{max(0, min(99, int(value))):02d}"
    rows = [0] * 8
    for row_index in range(5):
        rows[row_index + 1] = (DIGITS[text[0]][row_index] << 5) | (DIGITS[text[1]][row_index] << 1)
    return tuple(rows)


def read_temperature(path: Path = Path("/sys/class/thermal/thermal_zone0/temp")) -> int | None:
    try:
        millidegrees = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return max(0, min(99, round(millidegrees / 1000)))


def parse_cellular_quality(output: str) -> int | None:
    match = re.search(r"^modem\.generic\.signal-quality\.value\s*:\s*(\d+)", output, re.MULTILINE)
    if not match:
        return None
    return max(0, min(100, int(match.group(1))))


def parse_cellular_state(output: str) -> bool | None:
    match = re.search(r"^modem\.generic\.state\s*:\s*(\S+)", output, re.MULTILINE)
    if not match:
        return None
    state = match.group(1).lower()
    if state in {"failed", "disabled", "disabling", "locked"}:
        return False
    if state in {
        "initializing",
        "enabling",
        "enabled",
        "searching",
        "registered",
        "disconnecting",
        "connecting",
        "connected",
    }:
        return True
    return None


def read_cellular_status() -> tuple[bool | None, int | None]:
    try:
        result = subprocess.run(
            ["mmcli", "-m", "any", "-K"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    quality = parse_cellular_quality(result.stdout)
    online = parse_cellular_state(result.stdout)
    if online is None and quality is not None:
        online = True
    return online, quality


def read_cellular_quality() -> int | None:
    return read_cellular_status()[1]


def parse_network_uplink(output: str) -> str:
    match = re.search(r"\bdev\s+(\S+)", output)
    if not match:
        return "Offline"
    interface = match.group(1)
    if interface == "wlan0":
        return "WiFi"
    if interface in {"wwan0", "ppp0"} or interface.startswith(("wwan", "ppp")):
        return "Cellular"
    return "Offline"


def read_network_uplink() -> str:
    try:
        result = subprocess.run(
            ["ip", "route", "get", "8.8.8.8"],
            text=True,
            capture_output=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "Offline"
    return parse_network_uplink(result.stdout if result.returncode == 0 else "")


def parse_ap_client_count(output: str) -> int:
    """Count active PCS client neighbors while excluding fixed infrastructure."""
    clients: set[str] = set()
    for line in output.splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith("10.42.0."):
            continue
        if any(state in parts for state in ("FAILED", "INCOMPLETE")):
            continue
        try:
            host = int(parts[0].rsplit(".", 1)[1])
        except (ValueError, IndexError):
            continue
        if host not in {1, 2, 3}:
            clients.add(parts[0])
    return len(clients)


def read_ap_client_count() -> int | None:
    try:
        result = subprocess.run(
            ["ip", "neigh", "show", "dev", "eth0"],
            text=True,
            capture_output=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_ap_client_count(result.stdout) if result.returncode == 0 else None


def maidenhead_grid(latitude: float, longitude: float) -> str | None:
    """Return the same six-character Maidenhead locator used by PCS web status."""
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    lon = longitude + 180
    lat = latitude + 90
    letters = "ABCDEFGHIJKLMNOPQRSTUVWX"
    field_lon = int(lon // 20)
    field_lat = int(lat // 10)
    lon -= field_lon * 20
    lat -= field_lat * 10
    square_lon = int(lon // 2)
    square_lat = int(lat // 1)
    lon -= square_lon * 2
    lat -= square_lat
    subsquare_lon = int(lon / (2 / 24))
    subsquare_lat = int(lat / (1 / 24))
    return (
        letters[field_lon]
        + letters[field_lat]
        + str(square_lon)
        + str(square_lat)
        + letters[subsquare_lon].lower()
        + letters[subsquare_lat].lower()
    )


def satellite_count_from_sky(record: dict[str, object]) -> int | None:
    """Return gpsd's satellites-in-view count without retaining satellite details."""
    count = record.get("nSat")
    if isinstance(count, int) and not isinstance(count, bool):
        return max(0, min(99, count))
    satellites = record.get("satellites")
    if isinstance(satellites, list):
        return min(99, len(satellites))
    return None


def satellite_counts_from_sky(record: dict[str, object]) -> tuple[int | None, int | None]:
    """Return gpsd satellites in view and used from one SKY report."""
    viewed = satellite_count_from_sky(record)
    used = record.get("uSat")
    if isinstance(used, int) and not isinstance(used, bool):
        return viewed, max(0, min(99, used))
    satellites = record.get("satellites")
    if isinstance(satellites, list):
        used_count = sum(
            1 for satellite in satellites
            if isinstance(satellite, dict) and satellite.get("used") is True
        )
        return viewed, min(99, used_count)
    return viewed, None


def merge_gps_details(
    satellites_view: int | None,
    satellites_used: int | None,
    locked: bool | None,
    record: dict[str, object],
) -> tuple[int | None, int | None, bool | None]:
    """Keep paired counts from the fullest SKY report and the best TPV fix."""
    if record.get("class") == "SKY":
        candidate_view, candidate_used = satellite_counts_from_sky(record)
        if candidate_view is not None and (
            satellites_view is None
            or candidate_view > satellites_view
            or (candidate_view == satellites_view and (candidate_used or 0) > (satellites_used or 0))
        ):
            satellites_view = candidate_view
            satellites_used = candidate_used
    elif record.get("class") == "TPV":
        mode = record.get("mode")
        if isinstance(mode, int) and not isinstance(mode, bool):
            candidate_lock = mode >= 2
            locked = candidate_lock if locked is None else locked or candidate_lock
    return satellites_view, satellites_used, locked


def merge_gps_status(
    satellites: int | None,
    locked: bool | None,
    record: dict[str, object],
) -> tuple[int | None, bool | None]:
    """Keep the fullest SKY report and best TPV fix without retaining coordinates."""
    satellites, _used, locked = merge_gps_details(satellites, None, locked, record)
    return satellites, locked


def read_gps_details(
    host: str = "127.0.0.1",
    port: int = 2947,
) -> tuple[int | None, int | None, bool | None, str | None]:
    """Read gpsd counts, fix and grid; coordinates are neither retained nor logged."""
    deadline = time.monotonic() + 2.0
    satellites_view: int | None = None
    satellites_used: int | None = None
    locked: bool | None = None
    grid_square: str | None = None
    try:
        with socket.create_connection((host, port), timeout=1.0) as connection:
            connection.settimeout(0.5)
            connection.sendall(b'?WATCH={"enable":true,"json":true};\n')
            buffer = b""
            while time.monotonic() < deadline:
                try:
                    chunk = connection.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw_line, buffer = buffer.split(b"\n", 1)
                    try:
                        record = json.loads(raw_line.decode("utf-8", errors="replace"))
                    except (json.JSONDecodeError, UnicodeError):
                        continue
                    satellites_view, satellites_used, locked = merge_gps_details(
                        satellites_view,
                        satellites_used,
                        locked,
                        record,
                    )
                    if record.get("class") == "TPV":
                        mode = record.get("mode")
                        latitude = record.get("lat")
                        longitude = record.get("lon")
                        if (
                            isinstance(mode, int)
                            and not isinstance(mode, bool)
                            and mode >= 2
                            and isinstance(latitude, (int, float))
                            and not isinstance(latitude, bool)
                            and isinstance(longitude, (int, float))
                            and not isinstance(longitude, bool)
                        ):
                            grid_square = maidenhead_grid(float(latitude), float(longitude))
    except OSError:
        pass
    return satellites_view, satellites_used, locked, grid_square


def read_gps_status(host: str = "127.0.0.1", port: int = 2947) -> tuple[int | None, bool | None]:
    satellites_view, _satellites_used, locked, _grid_square = read_gps_details(host, port)
    return satellites_view, locked


def collect_stats() -> StatsSnapshot:
    cellular_online, cellular_quality = read_cellular_status()
    gps_satellites, gps_satellites_used, gps_locked, grid_square = read_gps_details()
    return StatsSnapshot(
        temperature_c=read_temperature(),
        cellular_quality=cellular_quality,
        gps_satellites=gps_satellites,
        gps_locked=gps_locked,
        cellular_online=cellular_online,
        gps_satellites_used=gps_satellites_used,
        network_uplink=read_network_uplink(),
        ap_clients=read_ap_client_count(),
        grid_square=grid_square,
    )


def read_uptime_seconds(path: Path = Path("/proc/uptime")) -> int | None:
    try:
        value = float(path.read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return max(0, int(value))


def format_uptime(seconds: int | None) -> str:
    if seconds is None:
        return "Up: --d --h --m"
    minutes = max(0, int(seconds)) // 60
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    return f"Up: {min(days, 999)}d {hours:02}h {minutes:02}m"


def lcd_status_pages(
    snapshot: StatsSnapshot,
    uptime_seconds: int | None,
) -> tuple[tuple[str, str], ...]:
    if snapshot.temperature_c is None:
        temperature_line = "--°C / --°F"
    else:
        temperature_f = round(snapshot.temperature_c * 9 / 5 + 32)
        temperature_line = f"{snapshot.temperature_c:02d}°C / {temperature_f:02d}°F"

    cellular_state = "On" if snapshot.cellular_online is True else "Off"
    cellular_quality = snapshot.cellular_quality if snapshot.cellular_quality is not None else 0

    if snapshot.gps_locked is True:
        gps_heading = "GPS Status: Lock"
    elif snapshot.gps_locked is False or snapshot.gps_satellites == 0:
        gps_heading = "GPS Status: NoFx"
    else:
        gps_heading = "GPS Status: Err"
    gps_view = "--" if snapshot.gps_satellites is None else f"{snapshot.gps_satellites:02d}"
    gps_used = "--" if snapshot.gps_satellites_used is None else f"{snapshot.gps_satellites_used:02d}"
    network_uplink = snapshot.network_uplink or "Offline"
    ap_clients = "--" if snapshot.ap_clients is None else str(snapshot.ap_clients)
    grid_square = snapshot.grid_square or "------"
    return (
        ("PCS Online", format_uptime(uptime_seconds)),
        ("Pi CPU Temp", temperature_line),
        ("Network Uplink", network_uplink),
        (f"Cell Status: {cellular_state}", f"Signal: {cellular_quality:03d}%"),
        (gps_heading, f"View {gps_view} Used {gps_used}"),
        (f"AP Clients: {ap_clients}", f"GridSq: {grid_square}"),
    )


def stats_frames(snapshot: StatsSnapshot) -> tuple[StatsFrame, ...]:
    if snapshot.gps_satellites == 0 or snapshot.gps_locked is False:
        gps_value = NO_FIX_ICON
    elif snapshot.gps_satellites is None or snapshot.gps_locked is None:
        gps_value = UNKNOWN_ICON
    else:
        gps_value = render_two_digits(snapshot.gps_satellites)
    return (
        StatsFrame("temperature_c", "icon", DEGREE_C_ICON),
        StatsFrame("temperature_c", "value", render_two_digits(snapshot.temperature_c)),
        StatsFrame("cellular_quality", "icon", SIGNAL_ICON),
        StatsFrame("cellular_quality", "value", render_two_digits(snapshot.cellular_quality)),
        StatsFrame("gps_satellites", "icon", SATELLITE_DISH_ICON),
        StatsFrame("gps_satellites", "value", gps_value),
    )


def run_stats(
    matrix: MatrixDisplay,
    *,
    once: bool = False,
    icon_seconds: float = 0.8,
    value_seconds: float = 1.5,
    cycle_pause: float = 0.3,
    collector: Callable[[], StatsSnapshot] = collect_stats,
    sleeper: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    while not should_stop():
        snapshot = collector()
        print(json.dumps(snapshot.as_dict(), separators=(",", ":")), flush=True)
        for frame in stats_frames(snapshot):
            if should_stop():
                break
            matrix.rows(frame.rows)
            sleeper(icon_seconds if frame.kind == "icon" else value_seconds)
        if once:
            break
        sleeper(cycle_pause)


def run_lcd_status(
    lcd: LcdDisplay,
    *,
    once: bool = False,
    page_seconds: float = 3.0,
    collector: Callable[[], StatsSnapshot] = collect_stats,
    uptime_reader: Callable[[], int | None] = read_uptime_seconds,
    sleeper: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    while not should_stop():
        snapshot = collector()
        pages = lcd_status_pages(snapshot, uptime_reader())
        print(
            json.dumps(
                {"snapshot": snapshot.as_dict(), "pages": [list(page) for page in pages]},
                separators=(",", ":"),
            ),
            flush=True,
        )
        for page in pages:
            if should_stop():
                break
            lcd.text(page)
            sleeper(page_seconds)
        if once:
            break


def dependency_report() -> dict[str, object]:
    model_path = Path("/proc/device-tree/model")
    model = ""
    try:
        model = model_path.read_text(encoding="utf-8").rstrip("\x00")
    except OSError:
        pass
    modules = {
        name: importlib.util.find_spec(name) is not None
        for name in ("gpiozero", "spidev", "rpi_ws281x")
    }
    return {
        "platform": platform.system(),
        "raspberry_pi_model": model,
        "gpio_header_available": bool(model),
        "spi0_device": Path("/dev/spidev0.0").exists(),
        "python_modules": modules,
        "effective_uid": os.geteuid() if hasattr(os, "geteuid") else None,
        "writes_performed": False,
    }


def pin_map_json() -> list[dict[str, object]]:
    return [asdict(pin) for pin in PIN_ASSIGNMENTS]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and commission PCS GPIO devices safely")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pins = subparsers.add_parser("pins", help="print the authoritative BCM/physical pin map")
    pins.add_argument("--json", action="store_true")

    check = subparsers.add_parser("check", help="read-only Raspberry Pi dependency and interface check")
    check.add_argument("--json", action="store_true")

    demo = subparsers.add_parser("demo", help="simulate a device pattern or explicitly apply it to hardware")
    demo.add_argument("target", choices=("all", "lcd", "matrix", "leds", "fan"))
    demo.add_argument("--hardware", action="store_true", help="select real Raspberry Pi hardware")
    demo.add_argument("--apply", action="store_true", help="confirm that GPIO writes are intended")
    demo.add_argument("--duration", type=float, default=3.0)
    demo.add_argument("--fan-duty", type=float)

    lcd = subparsers.add_parser("lcd", help="write two lines to the HD44780-compatible 16x2 LCD")
    lcd.add_argument("--line1", default="PCS ONLINE")
    lcd.add_argument("--line2", default="LCD DRIVER READY")
    lcd.add_argument("--hardware", action="store_true", help="select the real Raspberry Pi LCD")
    lcd.add_argument("--apply", action="store_true", help="confirm that LCD GPIO writes are intended")
    lcd.add_argument("--duration", type=float, default=0.0, help="seconds to hold GPIO ownership after writing")
    lcd.add_argument("--clear", action="store_true", help="clear the LCD before releasing GPIO")

    stats = subparsers.add_parser("stats", help="rotate useful live PCS statistics on the MAX7219")
    stats.add_argument("--hardware", action="store_true", help="select the real SPI0 MAX7219")
    stats.add_argument("--apply", action="store_true", help="confirm that matrix writes are intended")
    stats.add_argument("--once", action="store_true", help="show one complete rotation, then exit")
    stats.add_argument("--icon-seconds", type=float, default=0.8)
    stats.add_argument("--value-seconds", type=float, default=1.5)
    stats.add_argument("--cycle-pause", type=float, default=0.3)

    lcd_status = subparsers.add_parser("lcd-status", help="rotate live PCS status on the 16x2 LCD")
    lcd_status.add_argument("--hardware", action="store_true", help="select the real Raspberry Pi LCD")
    lcd_status.add_argument("--apply", action="store_true", help="confirm that LCD GPIO writes are intended")
    lcd_status.add_argument("--once", action="store_true", help="show one complete rotation, then exit")
    lcd_status.add_argument("--page-seconds", type=float, default=3.0)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "pins":
        if args.json:
            print(json.dumps(pin_map_json(), indent=2))
        else:
            for pin in PIN_ASSIGNMENTS:
                print(f"GPIO{pin.gpio:<2} pin {pin.physical:<2}  {pin.function:<18} {pin.owner}; {pin.status}")
        return 0

    if args.command == "check":
        report = dependency_report()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Platform:       {report['platform']}")
            print(f"Raspberry Pi:   {report['raspberry_pi_model'] or 'not detected'}")
            print(f"SPI0 / CE0:     {'available' if report['spi0_device'] else 'missing'}")
            for name, available in report["python_modules"].items():  # type: ignore[union-attr]
                print(f"Python {name:<10} {'available' if available else 'missing'}")
            print("GPIO writes:    none")
        return 0

    if args.hardware and not args.apply:
        raise SystemExit("ERROR: real GPIO operation requires both --hardware and --apply")
    if args.apply and not args.hardware:
        raise SystemExit("ERROR: --apply is valid only together with --hardware")

    if args.command == "stats":
        durations = (args.icon_seconds, args.value_seconds, args.cycle_pause)
        if any(duration < 0 for duration in durations):
            raise SystemExit("ERROR: stats durations cannot be negative")
        if not args.hardware:
            snapshot = collect_stats()
            print(json.dumps({
                "backend": "simulation",
                "snapshot": snapshot.as_dict(),
                "frames": [frame.as_dict() for frame in stats_frames(snapshot)],
                "writes_performed": False,
            }, indent=2))
            return 0

        stopped = False

        def request_stop(_signum: int, _frame: object) -> None:
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        matrix: Max7219 | None = None
        try:
            matrix = Max7219()
            run_stats(
                matrix,
                once=args.once,
                icon_seconds=args.icon_seconds,
                value_seconds=args.value_seconds,
                cycle_pause=args.cycle_pause,
                should_stop=lambda: stopped,
            )
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise SystemExit(f"ERROR: {error}") from error
        finally:
            if matrix is not None:
                matrix.close()
        return 0

    if args.command == "lcd-status":
        if args.page_seconds < 0:
            raise SystemExit("ERROR: --page-seconds cannot be negative")
        if not args.hardware:
            snapshot = collect_stats()
            print(json.dumps({
                "backend": "simulation",
                "snapshot": snapshot.as_dict(),
                "pages": [list(page) for page in lcd_status_pages(snapshot, read_uptime_seconds())],
                "writes_performed": False,
            }, indent=2))
            return 0

        stopped = False

        def request_lcd_stop(_signum: int, _frame: object) -> None:
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, request_lcd_stop)
        signal.signal(signal.SIGINT, request_lcd_stop)
        device: HD44780 | None = None
        try:
            device = HD44780()
            run_lcd_status(
                device,
                once=args.once,
                page_seconds=args.page_seconds,
                should_stop=lambda: stopped,
            )
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise SystemExit(f"ERROR: {error}") from error
        finally:
            if device is not None:
                device.close(clear=False)
        return 0

    if args.command == "lcd":
        if args.duration < 0:
            raise SystemExit("ERROR: --duration cannot be negative")
        lines = normalize_lcd_lines((args.line1, args.line2))
        if not args.hardware:
            print(json.dumps({
                "backend": "simulation",
                "device": "lcd",
                "pins": LCD_PINS,
                "lines": list(lines),
                "writes_performed": False,
            }, indent=2))
            return 0

        device: HD44780 | None = None
        try:
            device = HD44780()
            device.text(lines)
            time.sleep(args.duration)
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise SystemExit(f"ERROR: {error}") from error
        finally:
            if device is not None:
                device.close(clear=args.clear)
        return 0

    if args.duration < 0:
        raise SystemExit("ERROR: --duration cannot be negative")

    backend: Backend = RaspberryPiBackend() if args.hardware else MockBackend()
    try:
        run_demo(backend, args.target, duration=args.duration, fan_duty=args.fan_duty)
    except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"ERROR: {error}") from error
    if isinstance(backend, MockBackend):
        print(json.dumps({"backend": "simulation", "events": backend.events}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
