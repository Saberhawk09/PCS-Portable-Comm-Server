#!/usr/bin/env python3

"""Recover a stuck Dire Wolf APRS-IS session after a real uplink change."""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import NamedTuple


DEFAULT_CONFIG = "/etc/direwolf.conf"
DEFAULT_STATE = "/run/pcs-direwolf-uplink"
DEFAULT_RESTART_MARKER = "/run/pcs-direwolf-uplink-recovery.last"
DEFAULT_GRACE_SECONDS = 45
DEFAULT_VERIFY_SECONDS = 60
DEFAULT_COOLDOWN_SECONDS = 300
DEFAULT_APRS_IS_PORT = 14580


class CommandRunner:
    def run(self, arguments: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )


def _atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(f"{value}\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def default_interface(runner: CommandRunner) -> str:
    result = runner.run(["ip", "-4", "route", "get", "1.1.1.1"], timeout=5)
    if result.returncode != 0:
        return ""
    match = re.search(r"(?:^|\s)dev\s+(\S+)", result.stdout)
    return match.group(1) if match else ""


def aprs_is_server(path: str | os.PathLike[str]) -> tuple[str, int] | None:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parts = shlex.split(stripped, comments=True, posix=True)
        except ValueError:
            continue
        if not parts or parts[0].upper() != "IGSERVER" or len(parts) < 2:
            continue
        port = DEFAULT_APRS_IS_PORT
        if len(parts) >= 3:
            try:
                port = int(parts[2])
            except ValueError:
                return None
        if not 1 <= port <= 65535:
            return None
        return parts[1], port
    return None


def server_resolves(runner: CommandRunner, hostname: str) -> bool:
    return runner.run(["getent", "ahostsv4", hostname], timeout=10).returncode == 0


def direwolf_active(runner: CommandRunner) -> bool:
    return runner.run(["systemctl", "is-active", "--quiet", "direwolf.service"], timeout=10).returncode == 0


class Connection(NamedTuple):
    local: str
    local_port: int
    peer: str
    peer_port: int
    pid: int
    inode: str

    def describe(self) -> str:
        return f"[{self.local}]:{self.local_port} -> [{self.peer}]:{self.peer_port} pid={self.pid} inode={self.inode}"


def _endpoint(value: str) -> tuple[str, int]:
    address, port = value.rsplit(":", 1)
    return str(ipaddress.ip_address(address.strip("[]"))), int(port)


def direwolf_connections(runner: CommandRunner, port: int) -> set[Connection] | None:
    result = runner.run(["ss", "-H", "-t", "-n", "-p", "-e", "state", "established"], timeout=10)
    if result.returncode != 0:
        return None
    connections = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        owner = re.search(r'\("direwolf",pid=(\d+),', line)
        if owner is None:
            continue
        # ss with a state filter omits State; tolerate versions which include it.
        offset = 1 if fields[0] == "ESTAB" else 0
        try:
            local, local_port = _endpoint(fields[offset + 2])
            peer, peer_port = _endpoint(fields[offset + 3])
        except (ValueError, IndexError):
            return None  # Unknown ownership/identity must not certify freshness.
        if peer_port != port:
            continue
        inode = re.search(r"\bino:(\d+)\b", line)
        connections.add(Connection(local, local_port, peer, peer_port,
                                   int(owner.group(1)), inode.group(1) if inode else ""))
    return connections


def connection_route(runner: CommandRunner, connection: Connection, interface: str) -> tuple[bool, str]:
    family = "-4" if ipaddress.ip_address(connection.peer).version == 4 else "-6"
    result = runner.run(["ip", family, "route", "get", connection.peer], timeout=5)
    device = re.search(r"(?:^|\s)dev\s+(\S+)", result.stdout)
    source = re.search(r"(?:^|\s)src\s+(\S+)", result.stdout)
    if result.returncode or device is None or source is None:
        return False, "effective route/source unavailable"
    try:
        matches = ipaddress.ip_address(source.group(1)) == ipaddress.ip_address(connection.local)
    except ValueError:
        matches = False
    # IPv6 has its own effective default route and source selection.
    valid = matches and (family == "-6" or device.group(1) == interface)
    return valid, f"route {device.group(1)} src {source.group(1)}"


def _marker_age(path: Path, now: float) -> float | None:
    try:
        value = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return max(0.0, now - value)


class UplinkRecovery:
    def __init__(
        self,
        runner: CommandRunner,
        *,
        config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
        state_path: str | os.PathLike[str] = DEFAULT_STATE,
        restart_marker: str | os.PathLike[str] = DEFAULT_RESTART_MARKER,
        grace_seconds: int = DEFAULT_GRACE_SECONDS,
        verify_seconds: int = DEFAULT_VERIFY_SECONDS,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        sleep=time.sleep,
        monotonic=time.monotonic,
        wall_time=time.time,
    ) -> None:
        self.runner = runner
        self.config_path = Path(config_path)
        self.state_path = Path(state_path)
        self.restart_marker = Path(restart_marker)
        self.grace_seconds = grace_seconds
        self.verify_seconds = verify_seconds
        self.cooldown_seconds = cooldown_seconds
        self.sleep = sleep
        self.monotonic = monotonic
        self.wall_time = wall_time

    def record_current(self) -> str:
        interface = default_interface(self.runner)
        if not interface:
            return "No default IPv4 uplink is available; baseline unchanged."
        _atomic_write(self.state_path, interface)
        return f"Recorded Dire Wolf uplink baseline: {interface}."

    def _wait_for_connection(self, port: int, seconds: int, old: set[Connection], interface: str) -> bool:
        deadline = self.monotonic() + seconds
        while True:
            connections = direwolf_connections(self.runner, port)
            if connections is not None:
                for connection in sorted(connections - old):
                    valid, route = connection_route(self.runner, connection, interface)
                    if valid and default_interface(self.runner) == interface:
                        print(f"New APRS-IS socket: {connection.describe()}; {route}; recovery verified.", flush=True)
                        return True
            if self.monotonic() >= deadline:
                return False
            self.sleep(min(5, max(0, deadline - self.monotonic())))

    def recover(self) -> tuple[bool, str]:
        current = default_interface(self.runner)
        if not current:
            return True, "No default IPv4 uplink is available; Dire Wolf was not restarted."
        try:
            previous = self.state_path.read_text(encoding="utf-8").strip()
        except OSError:
            _atomic_write(self.state_path, current)
            return True, f"Initialized Dire Wolf uplink baseline at {current}."
        if previous == current:
            return True, f"Default uplink remains {current}; no Dire Wolf action required."

        _atomic_write(self.state_path, current)
        server = aprs_is_server(self.config_path)
        if server is None:
            return True, f"Uplink changed from {previous} to {current}; APRS-IS is not configured."
        if not direwolf_active(self.runner):
            return True, f"Uplink changed from {previous} to {current}; Dire Wolf is inactive."

        hostname, port = server
        old = direwolf_connections(self.runner, port)
        print(f"APRS-IS uplink change: {previous} -> {current}", flush=True)
        if old is None:
            return False, "Cannot capture APRS-IS socket identity; recovery was not attempted."
        for connection in sorted(old):
            _, route = connection_route(self.runner, connection, current)
            print(f"Old APRS-IS socket: {connection.describe()}; {route}", flush=True)
        if self._wait_for_connection(port, self.grace_seconds, old, current):
            return True, f"Dire Wolf reconnected to APRS-IS after uplink changed from {previous} to {current}."
        remaining = direwolf_connections(self.runner, port)
        if remaining is None:
            return False, "Cannot inspect APRS-IS sockets after grace period; recovery was not attempted."
        if old & remaining:
            print("Old APRS-IS socket still present after grace period.", flush=True)
        # Also reject any invalid connection first observed during grace after restart.
        old.update(remaining)
        if default_interface(self.runner) != current:
            return False, "Default uplink changed again during recovery; Dire Wolf was not restarted."
        if not server_resolves(self.runner, hostname):
            return True, f"APRS-IS DNS is unavailable after uplink changed to {current}; Dire Wolf remains running and will retry."

        now = self.wall_time()
        age = _marker_age(self.restart_marker, now)
        if age is not None and age < self.cooldown_seconds:
            return True, f"Dire Wolf recovery restart suppressed by the {self.cooldown_seconds}-second cooldown."

        if not direwolf_active(self.runner):
            return True, "Dire Wolf stopped during the recovery grace period; leaving it stopped."
        print("No fresh route-consistent APRS-IS connection; restarting Dire Wolf with PTT guard.", flush=True)
        # ExecStopPost starts the conflicting PTT guard. A single restart
        # transaction lets that guard cancel the pending engine start. Finish
        # stopping and settle the guard before requesting a separate start.
        for operation, unit, timeout in (
            ("stop", "direwolf.service", 30),
            ("start", "pcs-aprs-ptt-safe.service", 15),
            ("start", "direwolf.service", 90),
        ):
            result = self.runner.run(["systemctl", operation, unit], timeout=timeout)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip() or "systemctl returned an error"
                return False, f"Dire Wolf recovery {operation} {unit} failed: {detail}"
        _atomic_write(self.restart_marker, str(now))
        if not self._wait_for_connection(port, self.verify_seconds, old, current):
            return False, f"Dire Wolf restarted after the uplink change, but no fresh route-consistent APRS-IS connection appeared within {self.verify_seconds} seconds."
        return True, f"Restarted Dire Wolf after uplink changed from {previous} to {current}; fresh APRS-IS recovery verified."


def bounded_seconds(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed <= 600:
        raise argparse.ArgumentTypeError("seconds must be between 0 and 600")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record-current", action="store_true")
    mode.add_argument("--recover", action="store_true")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--restart-marker", default=DEFAULT_RESTART_MARKER)
    parser.add_argument("--grace-seconds", type=bounded_seconds, default=DEFAULT_GRACE_SECONDS)
    parser.add_argument("--verify-seconds", type=bounded_seconds, default=DEFAULT_VERIFY_SECONDS)
    parser.add_argument("--cooldown-seconds", type=bounded_seconds, default=DEFAULT_COOLDOWN_SECONDS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    recovery = UplinkRecovery(
        CommandRunner(),
        config_path=args.config,
        state_path=args.state,
        restart_marker=args.restart_marker,
        grace_seconds=args.grace_seconds,
        verify_seconds=args.verify_seconds,
        cooldown_seconds=args.cooldown_seconds,
    )
    if args.record_current:
        print(recovery.record_current(), flush=True)
        return 0
    ok, message = recovery.recover()
    print(message, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
