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
HEALTH_PATH = Path(os.environ.get("PCS_BUZZER_HEALTH", "/run/pcs-buzzer/health.json"))
HEALTH_MAX_AGE_SECONDS = 30
HEALTH_DEBOUNCE_SECONDS = 5.0


@dataclass(frozen=True)
class Tone:
    frequency: int
    seconds: float
    duty: float


SILENCE = Tone(0, 0.10, 0.0)
PATTERNS: dict[str, tuple[Tone, ...]] = {
    "post": (Tone(880, 0.10, 0.24),),
    "ok": (Tone(360, 0.18, 0.20), Tone(520, 0.16, 0.20), Tone(760, 0.12, 0.20)),
    "warn": (Tone(760, 0.10, 0.125), SILENCE, Tone(760, 0.10, 0.125), Tone(0, 2.7, 0)),
    "bad": (Tone(520, 0.42, 0.425), SILENCE, Tone(420, 0.42, 0.425), Tone(0, 1.3, 0)),
    "low_voltage": (Tone(700, 1.0, 0.625), Tone(0, 1.0, 0)),
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


def read_health_pattern(now: float | None = None) -> str | None:
    now = time.time() if now is None else now
    health = read_json(HEALTH_PATH)
    updated = health.get("updated_at_epoch")
    if not isinstance(updated, (int, float)) or updated > now + 5 or now - updated > HEALTH_MAX_AGE_SECONDS:
        return None
    alerts = health.get("alerts")
    if not isinstance(alerts, list):
        return None
    severities = {
        alert.get("severity")
        for alert in alerts
        if isinstance(alert, dict)
    }
    if "critical" in severities:
        return "bad"
    if "warning" in severities:
        return "warn"
    return "silent"


def raw_health_pattern(now: float | None = None) -> str:
    return read_health_pattern(now) or "silent"


@dataclass
class HealthDebouncer:
    candidate: str = "silent"
    candidate_since: float = 0.0
    confirmed: str = "silent"

    def update(self, pattern: str, now: float) -> str:
        if pattern != self.candidate:
            self.candidate = pattern
            self.candidate_since = now
        elif pattern != self.confirmed and now - self.candidate_since >= HEALTH_DEBOUNCE_SECONDS:
            self.confirmed = pattern
        return self.confirmed


@dataclass
class OnlineChimeGuard:
    pending: bool = True
    healthy_since: float | None = None

    def should_play(self, *, health_available: bool, raw_health: str, effective_pattern: str, now: float) -> bool:
        if not self.pending:
            return False
        if health_available and raw_health == "silent" and effective_pattern == "silent":
            if self.healthy_since is None:
                self.healthy_since = now
            elif now - self.healthy_since >= HEALTH_DEBOUNCE_SECONDS:
                self.pending = False
                return True
        else:
            self.healthy_since = None
        return False


def requested_pattern(now: float | None = None, health_pattern: str = "silent") -> str:
    now = time.time() if now is None else now
    power = read_json(POWER_PATH)
    if power.get("low_voltage", {}).get("active") is True:
        return "low_voltage"
    request = read_json(REQUEST_PATH)
    pattern = str(request.get("pattern", "silent"))
    expires = request.get("expires_at_epoch")
    if pattern not in PRIORITY or (isinstance(expires, (int, float)) and now > expires):
        pattern = "silent"
    if health_pattern in {"warn", "bad"} and PRIORITY[health_pattern] > PRIORITY[pattern]:
        pattern = health_pattern
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
    health = HealthDebouncer()
    online_chime = OnlineChimeGuard()
    health_available = False
    raw_health = "silent"
    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True
    def current_pattern() -> str:
        nonlocal health_available, raw_health
        now = time.time()
        sample = read_health_pattern(now)
        health_available = sample is not None
        raw_health = sample or "silent"
        confirmed_health = health.update(raw_health, now)
        return requested_pattern(now, confirmed_health)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        play(output, "post", sleeper=sleeper, interrupted=lambda: stopped)
        last = "silent"
        while not stopped:
            pattern = current_pattern()
            now = time.time()
            if online_chime.should_play(
                health_available=health_available,
                raw_health=raw_health,
                effective_pattern=pattern,
                now=now,
            ):
                play(output, "ok", sleeper=sleeper, interrupted=lambda: stopped)
                last = "ok"
                continue
            if pattern in {"low_voltage", "bad", "warn"}:
                play(output, pattern, sleeper=sleeper, interrupted=lambda: stopped or PRIORITY[current_pattern()] > PRIORITY[pattern])
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
