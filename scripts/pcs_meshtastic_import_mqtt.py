#!/usr/bin/env python3
"""Import MQTT credentials from the local Meshtastic radio without printing them."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import Sequence


def quote_environment_value(value: object) -> str:
    """Quote one value for a systemd EnvironmentFile."""

    text = str(value)
    if "\x00" in text or "\n" in text or "\r" in text:
        raise ValueError("MQTT credential contains an unsupported control character")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_credentials(path: str | os.PathLike[str], username: object, password: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"PCS_MESHTASTIC_MQTT_USERNAME={quote_environment_value(username)}\n")
            handle.write(f"PCS_MESHTASTIC_MQTT_PASSWORD={quote_environment_value(password)}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, target)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def import_from_radio(device: str, output: str, timeout: int) -> None:
    from meshtastic.ble_interface import BLEInterface

    interface = None
    try:
        interface = BLEInterface(device, noNodes=True, timeout=timeout)
        mqtt = interface.localNode.moduleConfig.mqtt
        write_credentials(output, mqtt.username, mqtt.password)
    finally:
        if interface is not None:
            interface.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=60)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    import_from_radio(args.device, args.output, args.timeout)
    print("MQTT credentials imported without displaying their values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
