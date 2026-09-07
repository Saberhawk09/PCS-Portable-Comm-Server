#!/usr/bin/env python3
"""PCS dual-INA226 power monitor and persistent low-voltage protection.

The service is inert unless explicitly configured.  It publishes an atomic,
non-secret JSON snapshot for the dashboard, self-test, displays, and buzzer.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol

CONFIG_PATH = Path(os.environ.get("PCS_POWER_CONFIG", "/etc/pcs/power-monitor.json"))
STATUS_PATH = Path(os.environ.get("PCS_POWER_STATUS", "/run/pcs-power-monitor/status.json"))
CONVERSION_SETTLE_SECONDS = 0.05


class Bus(Protocol):
    def read_word_data(self, address: int, register: int) -> int: ...
    def write_word_data(self, address: int, register: int, value: int) -> None: ...
    def close(self) -> None: ...


def swap16(value: int) -> int:
    return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)


def signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


@dataclass(frozen=True)
class MonitorConfig:
    name: str
    address: int
    shunt_ohms: float
    max_current_amps: float


@dataclass(frozen=True)
class Reading:
    online: bool
    voltage: float | None = None
    current: float | None = None
    power: float | None = None
    error: str | None = None


class Ina226:
    def __init__(
        self,
        bus: Bus,
        config: MonitorConfig,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.bus = bus
        self.config = config
        self.current_lsb = config.max_current_amps / 32768.0
        calibration = max(1, min(0xFFFF, int(0.00512 / (self.current_lsb * config.shunt_ohms))))
        # 16 averages, 1.1 ms bus/shunt conversions, continuous shunt+bus.
        self._write(0x00, 0x4527)
        self._write(0x05, calibration)
        sleeper(CONVERSION_SETTLE_SECONDS)

    def _read(self, register: int) -> int:
        return swap16(self.bus.read_word_data(self.config.address, register))

    def _write(self, register: int, value: int) -> None:
        self.bus.write_word_data(self.config.address, register, swap16(value))

    def reading(self) -> Reading:
        voltage = self._read(0x02) * 0.00125
        current = signed16(self._read(0x04)) * self.current_lsb
        power = self._read(0x03) * self.current_lsb * 25.0
        values = (voltage, current, power)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("non-finite INA226 reading")
        return Reading(True, round(voltage, 3), round(current, 3), round(power, 3))


def parse_address(value: object) -> int:
    address = int(str(value), 0)
    if not 0x40 <= address <= 0x4F:
        raise ValueError("INA226 address must be from 0x40 to 0x4f")
    return address


def load_config(path: Path = CONFIG_PATH) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("version") != 1:
        raise ValueError("unsupported power-monitor configuration version")
    monitors = raw.get("monitors")
    if not isinstance(monitors, dict) or "input" not in monitors:
        raise ValueError("input monitor is required")
    if not set(monitors).issubset({"input", "rail_5v", "rail_12v"}):
        raise ValueError("unsupported monitor role")
    parsed: dict[str, MonitorConfig] = {}
    addresses: set[int] = set()
    for name in monitors:
        item = monitors[name]
        address = parse_address(item["address"])
        if address in addresses:
            raise ValueError("INA226 monitors must use unique addresses")
        addresses.add(address)
        shunt = float(item["shunt_ohms"])
        maximum = float(item["max_current_amps"])
        if not 0 < shunt <= 1 or not 0 < maximum <= 100:
            raise ValueError("invalid shunt or maximum-current calibration")
        parsed[name] = MonitorConfig(name, address, shunt, maximum)
    result = dict(raw)
    result["monitors"] = parsed
    source = str(raw.get("source_mode", "auto")).lower()
    if source not in {"auto", "12v", "24v", "disabled"}:
        raise ValueError("source_mode must be auto, 12v, 24v, or disabled")
    result["source_mode"] = source
    result["low_voltage_threshold"] = float(raw.get("low_voltage_threshold", 11.5))
    result["low_voltage_hysteresis"] = float(raw.get("low_voltage_hysteresis", 0.3))
    if not 8.0 <= result["low_voltage_threshold"] <= 15.0:
        raise ValueError("low_voltage_threshold must be from 8 to 15 volts")
    if not 0.05 <= result["low_voltage_hysteresis"] <= 2.0:
        raise ValueError("low_voltage_hysteresis must be from 0.05 to 2 volts")
    confirm_samples = raw.get("confirm_samples", 3)
    shutdown_delay = raw.get("shutdown_delay_seconds", 90)
    if isinstance(confirm_samples, bool) or not isinstance(confirm_samples, int) or not 2 <= confirm_samples <= 30:
        raise ValueError("confirm_samples must be an integer from 2 to 30")
    if isinstance(shutdown_delay, bool) or not isinstance(shutdown_delay, int) or not 30 <= shutdown_delay <= 3600:
        raise ValueError("shutdown_delay_seconds must be an integer from 30 to 3600")
    result["confirm_samples"] = confirm_samples
    result["shutdown_delay_seconds"] = shutdown_delay
    allow_shutdown = raw.get("allow_shutdown", False)
    if not isinstance(allow_shutdown, bool):
        raise ValueError("allow_shutdown must be a JSON boolean")
    result["allow_shutdown"] = allow_shutdown
    result["poll_seconds"] = float(raw.get("poll_seconds", 2.0))
    if not 0.25 <= result["poll_seconds"] <= 60:
        raise ValueError("poll_seconds must be from 0.25 to 60")
    return result


def open_bus(number: int = 1) -> Bus:
    try:
        from smbus2 import SMBus
    except ImportError:
        from smbus import SMBus  # type: ignore[no-redef]
    return SMBus(number)


def safe_read(bus: Bus, config: MonitorConfig) -> Reading:
    try:
        return Ina226(bus, config).reading()
    except (OSError, ValueError) as error:
        return Reading(False, error=f"{type(error).__name__}: {error}")


def reading_status(name: str, reading: Reading, monitor: MonitorConfig, low_threshold: float = 11.5) -> str:
    if not reading.online or reading.voltage is None or reading.current is None or reading.power is None:
        return "warn"
    if reading.voltage < 0 or reading.current < -0.25 or reading.power < -1:
        return "bad"
    if name == "rail_5v":
        if reading.voltage < 4.65 or reading.voltage > 5.35:
            return "bad"
        if reading.voltage < 4.85 or reading.voltage > 5.20:
            return "warn"
    if reading.current > monitor.max_current_amps:
        return "bad"
    if reading.current > 0.8 * monitor.max_current_amps or reading.current < -0.05:
        return "warn"
    if name == "input":
        if reading.voltage < 8.0 or reading.voltage > 36.0:
            return "bad"
        if reading.voltage < low_threshold or reading.voltage > 30.0:
            return "warn"
    return "ok"


class LowVoltageGuard:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.nominal: str | None = None
        self.low_samples = 0
        self.started: float | None = None

    def update(self, voltage: float | None, now: float) -> tuple[bool, int | None]:
        if voltage is None or not math.isfinite(voltage):
            return self.started is not None, self.remaining(now)
        mode = self.config["source_mode"]
        if mode == "auto" and voltage >= 10.0:
            self.nominal = "24v" if voltage >= 18.0 else "12v"
        effective = self.nominal if mode == "auto" else mode
        if effective != "12v":
            self.low_samples = 0
            self.started = None
            return False, None
        threshold = self.config["low_voltage_threshold"]
        if voltage < threshold:
            self.low_samples += 1
            if self.low_samples >= self.config["confirm_samples"] and self.started is None:
                self.started = now
        elif voltage >= threshold + self.config["low_voltage_hysteresis"]:
            self.low_samples = 0
            self.started = None
        return self.started is not None, self.remaining(now)

    def remaining(self, now: float) -> int | None:
        if self.started is None:
            return None
        return max(0, math.ceil(self.config["shutdown_delay_seconds"] - (now - self.started)))


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def collect(bus: Bus, config: dict, guard: LowVoltageGuard, now: float) -> dict:
    readings = {name: safe_read(bus, monitor) for name, monitor in config["monitors"].items()}
    low, remaining = guard.update(readings["input"].voltage, now)
    estimated = None
    rail_5v = readings.get("rail_5v")
    if readings["input"].power is not None and rail_5v is not None and rail_5v.power is not None:
        estimated = round(max(0.0, readings["input"].power - rail_5v.power), 3)
    states = {
        name: reading_status(name, value, config["monitors"][name], config["low_voltage_threshold"])
        for name, value in readings.items()
    }
    overall = "bad" if low or "bad" in states.values() else "warn" if "warn" in states.values() else "ok"
    return {
        "version": 1,
        "collected_at_epoch": int(now),
        "status": overall,
        "source_mode": config["source_mode"],
        "detected_nominal_source": guard.nominal,
        "configured_roles": list(readings),
        "monitors": {name: {**asdict(value), "status": states[name], "address": f"0x{config['monitors'][name].address:02x}"} for name, value in readings.items()},
        "estimated_non_5v_power": estimated,
        "estimated_non_5v_note": "Estimate includes DC/DC conversion losses; not an exact 12V rail measurement.",
        "low_voltage": {
            "active": low,
            "threshold": config["low_voltage_threshold"],
            "hysteresis": config["low_voltage_hysteresis"],
            "remaining_seconds": remaining,
            "shutdown_armed": config["allow_shutdown"],
        },
    }


def run_service(config: dict, *, status_path: Path = STATUS_PATH, bus_factory: Callable[[], Bus] = open_bus, sleeper: Callable[[float], None] = time.sleep) -> None:
    guard = LowVoltageGuard(config)
    shutdown_requested = False
    bus = bus_factory()
    try:
        while True:
            now = time.monotonic()
            status = collect(bus, config, guard, now)
            status["collected_at_epoch"] = int(time.time())
            atomic_json(status_path, status)
            remaining = status["low_voltage"]["remaining_seconds"]
            if remaining == 0 and config["allow_shutdown"] and not shutdown_requested:
                shutdown_requested = True
                subprocess.run(["systemctl", "poweroff"], check=False)
            if not status["low_voltage"]["active"]:
                shutdown_requested = False
            sleeper(float(config.get("poll_seconds", 2.0)))
    finally:
        bus.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("once", "service", "check-config"))
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--status", type=Path, default=STATUS_PATH)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        parser.error(f"invalid power-monitor configuration: {error}")
    if args.command == "check-config":
        print("Power-monitor configuration is valid.")
        return 0
    if args.command == "service":
        run_service(config, status_path=args.status)
        return 0
    bus = open_bus()
    try:
        value = collect(bus, config, LowVoltageGuard(config), time.monotonic())
    finally:
        bus.close()
    print(json.dumps(value, indent=2))
    return 0 if all(item["online"] for item in value["monitors"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
