#!/usr/bin/env python3
"""Named, priority-arbitrated PCS passive-buzzer patterns on active-low GPIO13."""

from __future__ import annotations

import argparse
import json
import os
import signal
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

GPIO = 13
REQUEST_PATH = Path(os.environ.get("PCS_BUZZER_REQUEST", "/run/pcs-buzzer/request.json"))
POWER_PATH = Path(os.environ.get("PCS_POWER_STATUS", "/run/pcs-power-monitor/status.json"))
MUTE_PATH = Path(os.environ.get("PCS_BUZZER_MUTE", "/run/pcs-buzzer/muted"))


@dataclass(frozen=True)
class Tone:
    frequency: int
    seconds: float
    duty: float


SILENCE = Tone(0, 0.10, 0.0)
PATTERNS: dict[str, tuple[Tone, ...]] = {
    "post": (Tone(880, 0.10, 0.24),),
    "ok": (Tone(360, 0.18, 0.20), Tone(520, 0.16, 0.20), Tone(760, 0.12, 0.20)),
    "warn": (Tone(760, 0.10, 0.10), SILENCE, Tone(760, 0.10, 0.10), Tone(0, 2.7, 0)),
    "bad": (Tone(520, 0.42, 0.34), SILENCE, Tone(420, 0.42, 0.34), Tone(0, 1.3, 0)),
    "low_voltage": (Tone(700, 1.0, 0.50), Tone(0, 1.0, 0)),
}
PRIORITY = {"silent": 0, "ok": 1, "post": 1, "warn": 2, "bad": 3, "low_voltage": 4}


class Output(Protocol):
    def tone(self, frequency: int, duty: float) -> None: ...
    def off(self) -> None: ...
    def close(self) -> None: ...


class GpioOutput:
    def __init__(self) -> None:
        from gpiozero import PWMOutputDevice
        # active_high=False makes logical OFF a physical high level, matching
        # the module's PNP active-low input. External 10k pull-up is still required.
        self.device = PWMOutputDevice(GPIO, active_high=False, initial_value=0, frequency=440)

    def tone(self, frequency: int, duty: float) -> None:
        self.device.value = 0
        self.device.frequency = frequency
        self.device.value = max(0.0, min(1.0, duty))

    def off(self) -> None:
        self.device.value = 0

    def close(self) -> None:
        self.off()
        self.device.close()


class PrintOutput:
    def tone(self, frequency: int, duty: float) -> None:
        print(json.dumps({"frequency": frequency, "duty": duty}), flush=True)
    def off(self) -> None:
        print(json.dumps({"frequency": 0, "duty": 0}), flush=True)
    def close(self) -> None:
        self.off()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"))
            handle.write("\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def requested_pattern(now: float = time.time()) -> str:
    power = read_json(POWER_PATH)
    if power.get("low_voltage", {}).get("active") is True:
        return "low_voltage"
    request = read_json(REQUEST_PATH)
    pattern = str(request.get("pattern", "silent"))
    expires = request.get("expires_at_epoch")
    if pattern not in PRIORITY or (isinstance(expires, (int, float)) and now > expires):
        return "silent"
    if MUTE_PATH.exists() and pattern in {"warn", "bad"}:
        return "silent"
    return pattern


def play(output: Output, pattern: str, *, sleeper: Callable[[float], None] = time.sleep, interrupted: Callable[[], bool] = lambda: False) -> None:
    for tone in PATTERNS[pattern]:
        if interrupted():
            break
        if tone.frequency:
            output.tone(tone.frequency, tone.duty)
        else:
            output.off()
        sleeper(tone.seconds)
    output.off()


def serve(output: Output, *, sleeper: Callable[[float], None] = time.sleep) -> None:
    stopped = False
    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        play(output, "post", sleeper=sleeper, interrupted=lambda: stopped)
        last = "silent"
        while not stopped:
            pattern = requested_pattern()
            if pattern in {"low_voltage", "bad", "warn"}:
                play(output, pattern, sleeper=sleeper, interrupted=lambda: stopped or PRIORITY[requested_pattern()] > PRIORITY[pattern])
            elif pattern in {"ok", "post"} and pattern != last:
                play(output, pattern, sleeper=sleeper, interrupted=lambda: stopped)
            else:
                output.off()
                sleeper(0.25)
            last = pattern
    finally:
        output.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    play_parser = sub.add_parser("play")
    play_parser.add_argument("pattern", choices=PATTERNS)
    play_parser.add_argument("--hardware", action="store_true")
    request = sub.add_parser("request")
    request.add_argument("pattern", choices=("silent", *PATTERNS))
    request.add_argument("--seconds", type=int, default=0, help="expire after N seconds; zero persists until replaced")
    sub.add_parser("mute")
    sub.add_parser("unmute")
    service = sub.add_parser("service")
    service.add_argument("--simulate", action="store_true")
    args = parser.parse_args()
    if args.command == "request":
        value = {"version": 1, "pattern": args.pattern}
        if args.seconds > 0:
            value["expires_at_epoch"] = int(time.time()) + args.seconds
        atomic_json(REQUEST_PATH, value)
        return 0
    if args.command == "mute":
        MUTE_PATH.parent.mkdir(parents=True, exist_ok=True)
        MUTE_PATH.touch(mode=0o644, exist_ok=True)
        return 0
    if args.command == "unmute":
        MUTE_PATH.unlink(missing_ok=True)
        return 0
    if args.command == "play":
        output: Output = GpioOutput() if args.hardware else PrintOutput()
        play(output, args.pattern)
        output.close()
    else:
        output = PrintOutput() if args.simulate else GpioOutput()
        serve(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
