#!/usr/bin/env python3
"""Read or explicitly collect a privacy-preserving Meshtastic status snapshot."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_STATUS_FILE = "/var/lib/pcs-meshtastic/status.json"
DEFAULT_RECENT_SECONDS = 15 * 60
SCHEMA_VERSION = 1


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _attribute(value: Any, name: str, default: Any = None) -> Any:
    return getattr(value, name, default) if value is not None else default


def _iso_time(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _local_node(nodes: Mapping[str, Any], local_number: int | None) -> Mapping[str, Any]:
    if local_number is None:
        return {}
    for node in nodes.values():
        node_map = _mapping(node)
        if node_map.get("num") == local_number:
            return node_map
    return {}


def build_status(
    nodes: Mapping[str, Any] | None,
    my_info: Any = None,
    metadata: Any = None,
    *,
    now: float | None = None,
    recent_seconds: int = DEFAULT_RECENT_SECONDS,
) -> dict[str, Any]:
    """Build a summary without remote identities, positions, or message content."""

    collected_epoch = float(time.time() if now is None else now)
    node_map = nodes if isinstance(nodes, Mapping) else {}
    local_number_raw = _attribute(my_info, "my_node_num")
    try:
        local_number = int(local_number_raw) if local_number_raw is not None else None
    except (TypeError, ValueError):
        local_number = None

    local = _local_node(node_map, local_number)
    user = _mapping(local.get("user"))
    metrics = _mapping(local.get("deviceMetrics"))
    environment = _mapping(local.get("environmentMetrics"))
    local_stats = _mapping(local.get("localStats"))

    remote_nodes = [
        _mapping(node)
        for node in node_map.values()
        if local_number is None or _mapping(node).get("num") != local_number
    ]
    recent_cutoff = collected_epoch - max(0, recent_seconds)
    recent_nodes = 0
    last_mesh_epoch: float | None = None
    for node in remote_nodes:
        last_heard = _number(node.get("lastHeard"))
        if last_heard is None or last_heard <= 0:
            continue
        if last_heard >= recent_cutoff:
            recent_nodes += 1
        if last_mesh_epoch is None or last_heard > last_mesh_epoch:
            last_mesh_epoch = last_heard

    battery_raw = _number(metrics.get("batteryLevel"))
    externally_powered = battery_raw in {0.0, 101.0}
    battery_percent = None
    if battery_raw is not None and not externally_powered:
        battery_percent = max(0, min(100, round(battery_raw)))

    firmware = (
        _attribute(metadata, "firmware_version")
        or _attribute(my_info, "firmware_version")
        or "unknown"
    )
    hardware = user.get("hwModel") or _attribute(metadata, "hw_model") or "unknown"
    temperature_c = _number(environment.get("temperature"))
    humidity_percent = _number(environment.get("relativeHumidity"))
    environment_epoch = _number(environment.get("time"))

    return {
        "schema_version": SCHEMA_VERSION,
        "state": "connected",
        "transport": "bluetooth-le",
        "collected_at": _iso_time(collected_epoch),
        "collected_at_epoch": int(collected_epoch),
        "device": {
            "long_name": user.get("longName") or "unknown",
            "short_name": user.get("shortName") or "unknown",
            "hardware": str(hardware),
            "firmware": str(firmware),
            "power": "external" if externally_powered else "battery" if battery_percent is not None else "unknown",
            "battery_percent": battery_percent,
            "voltage": _number(metrics.get("voltage")),
            "channel_utilization_percent": _number(metrics.get("channelUtilization")),
            "air_utilization_tx_percent": _number(metrics.get("airUtilTx")),
        },
        "case_environment": {
            "source": "local-meshtastic-node",
            "temperature_c": temperature_c,
            "temperature_f": round((temperature_c * 9 / 5) + 32, 1) if temperature_c is not None else None,
            "humidity_percent": humidity_percent,
            "sampled_at": _iso_time(environment_epoch) if environment_epoch and environment_epoch > 0 else None,
            "sample_age_seconds": max(0, int(collected_epoch - environment_epoch))
            if environment_epoch and environment_epoch > 0
            else None,
        },
        "mesh": {
            "known_remote_nodes": len(remote_nodes),
            "recent_remote_nodes": recent_nodes,
            "recent_window_seconds": int(recent_seconds),
            "last_heard_at": _iso_time(last_mesh_epoch) if last_mesh_epoch else None,
            "received_packets": int(_number(local_stats.get("numPacketsRx")) or 0),
            "transmitted_packets": int(_number(local_stats.get("numPacketsTx")) or 0),
        },
        "privacy": {
            "messages_stored": False,
            "remote_identities_stored": False,
            "positions_stored": False,
            "channel_keys_stored": False,
        },
    }


def classify_error(exc: BaseException, *, now: float | None = None) -> dict[str, Any]:
    """Return a stable error without leaking an address or library traceback."""

    collected_epoch = float(time.time() if now is None else now)
    text = str(exc).lower()
    if "not found" in text or "no meshtastic" in text:
        reason = "device-not-found"
    elif "pair" in text or "authentication" in text or "not authorized" in text:
        reason = "pairing-required"
    elif "bluez" in text or "bluetooth" in text or "org.bluez" in text:
        reason = "bluetooth-unavailable"
    elif "timeout" in text or "timed out" in text:
        reason = "connection-timeout"
    else:
        reason = "connection-failed"

    return {
        "schema_version": SCHEMA_VERSION,
        "state": "error",
        "transport": "bluetooth-le",
        "collected_at": _iso_time(collected_epoch),
        "collected_at_epoch": int(collected_epoch),
        "reason": reason,
        "privacy": {
            "messages_stored": False,
            "remote_identities_stored": False,
            "positions_stored": False,
            "channel_keys_stored": False,
        },
    }


def write_status(path: str | os.PathLike[str], status: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(status, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, target)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def read_status(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a gateway snapshot without opening or disturbing the radio transport."""

    with Path(path).open("r", encoding="utf-8") as source:
        status = json.load(source)
    if not isinstance(status, dict):
        raise ValueError("status snapshot is not a JSON object")
    if status.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("status snapshot has an unsupported schema version")
    return status


def collect(device: str, timeout: int, recent_seconds: int) -> dict[str, Any]:
    from meshtastic.ble_interface import BLEInterface

    interface = None
    try:
        interface = BLEInterface(device, noNodes=True, timeout=timeout)
        return build_status(
            interface.nodes,
            interface.myInfo,
            interface.metadata,
            recent_seconds=recent_seconds,
        )
    finally:
        if interface is not None:
            interface.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device",
        default=os.environ.get("PCS_MESHTASTIC_DEVICE", ""),
        help="Paired Meshtastic BLE name or address",
    )
    parser.add_argument(
        "--status-file",
        default=os.environ.get("PCS_MESHTASTIC_STATUS_FILE", DEFAULT_STATUS_FILE),
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--recent-seconds", type=int, default=DEFAULT_RECENT_SECONDS)
    parser.add_argument(
        "--collect-ble",
        action="store_true",
        help="open the configured BLE node and replace the shared status snapshot",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="return failure unless the existing gateway snapshot is connected",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.collect_ble:
        try:
            status = read_status(args.status_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: Meshtastic gateway status is unavailable ({exc}).")
            return 1
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0 if not args.check or status.get("state") == "connected" else 1

    if not args.device or not args.device.strip():
        status = classify_error(ValueError("device not found"))
        status["reason"] = "device-not-configured"
        write_status(args.status_file, status)
        print("ERROR: PCS_MESHTASTIC_DEVICE is not configured.")
        return 2

    try:
        status = collect(args.device.strip(), args.timeout, args.recent_seconds)
    except Exception as exc:  # Hardware/library failures must still update status.
        status = classify_error(exc)
        write_status(args.status_file, status)
        print(f"ERROR: Meshtastic BLE collection failed ({status['reason']}).")
        return 1

    write_status(args.status_file, status)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
