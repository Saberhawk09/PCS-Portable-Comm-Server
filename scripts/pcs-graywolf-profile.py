#!/usr/bin/env python3
"""Provision and verify the PCS Dire Wolf-equivalent Graywolf profile safely."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def request(self, method: str, path: str, payload: object | None = None) -> object:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}/api/{path.lstrip('/')}", data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path}: HTTP {error.code}: {detail}") from error
        return json.loads(raw) if raw else None


def load_or_create_credentials(api: Api, path: Path) -> dict[str, str]:
    setup = api.request("GET", "auth/setup")
    if not isinstance(setup, dict):
        raise RuntimeError("Graywolf returned an invalid setup response")
    if path.exists():
        credentials = json.loads(path.read_text(encoding="utf-8"))
    elif setup.get("needs_setup"):
        credentials = {"username": "pcs-admin", "password": secrets.token_hex(24)}
        path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        path.write_text(json.dumps(credentials, separators=(",", ":")) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    else:
        raise RuntimeError(f"Graywolf is initialized but the credential file is missing: {path}")
    if setup.get("needs_setup"):
        api.request("POST", "auth/setup", credentials)
    api.request("POST", "auth/login", credentials)
    return credentials


def require_empty(api: Api, endpoints: tuple[str, ...]) -> None:
    occupied = []
    for endpoint in endpoints:
        value = api.request("GET", endpoint)
        if isinstance(value, list) and value:
            occupied.append(endpoint)
    if occupied:
        raise RuntimeError("refusing to add a duplicate profile; non-empty resources: " + ", ".join(occupied))


def stage_profile(api: Api, args: argparse.Namespace) -> dict[str, int]:
    require_empty(api, ("audio-devices", "channels", "ptt", "tx-timing", "kiss", "digipeater/rules", "beacons"))
    api.request("PUT", "station/config", {"callsign": args.callsign})

    common_audio = {
        "source_type": "soundcard",
        "sample_rate": 48000,
        "channels": 1,
        "format": "s16le",
    }
    input_device = api.request(
        "POST",
        "audio-devices",
        {
            **common_audio,
            "name": "PCS APRS input",
            "direction": "input",
            "device_path": args.audio_input_device,
            "gain_db": 0,
        },
    )
    output_device = api.request(
        "POST",
        "audio-devices",
        {
            **common_audio,
            "name": "PCS APRS output",
            "direction": "output",
            "device_path": args.audio_output_device,
            "gain_db": -0.001,
        },
    )
    if not isinstance(input_device, dict) or not isinstance(output_device, dict):
        raise RuntimeError("Graywolf did not return audio device identifiers")
    input_id = int(input_device["id"])
    output_id = int(output_device["id"])
    api.request("PUT", f"audio-devices/{output_id}/gain", {"gain_db": 0})

    channel = api.request(
        "POST",
        "channels",
        {
            "name": "PCS 2m APRS",
            "mode": "aprs",
            "input_device_id": input_id,
            "input_channel": 0,
            "output_device_id": output_id,
            "output_channel": 0,
            "modem_type": "afsk",
            "bit_rate": 1200,
            "mark_freq": 1200,
            "space_freq": 2200,
            "profile": "A",
            "num_slicers": 1,
            "fix_bits": "none",
            "fx25_encode": False,
            "il2p_encode": False,
            "num_decoders": 1,
            "decoder_offset": 0,
            "enabled": False,
        },
    )
    if not isinstance(channel, dict):
        raise RuntimeError("Graywolf did not return a channel identifier")
    channel_id = int(channel["id"])

    api.request(
        "POST",
        "ptt",
        {
            "channel_id": channel_id,
            "method": "gpio",
            "device_path": "/dev/gpiochip0",
            "gpio_pin": 0,
            "ptt_method": 0,
            "gpio_line": args.gpio_line,
            "invert": False,
            "slot_time_ms": 100,
            "persist": 63,
            "dwait_ms": 0,
        },
    )
    api.request(
        "POST",
        "tx-timing",
        {
            "channel": channel_id,
            "tx_delay_ms": args.tx_delay_ms,
            "tx_tail_ms": args.tx_tail_ms,
            "slot_ms": 100,
            "persist": 63,
            "full_dup": False,
            "rate_1min": 6,
            "rate_5min": 10,
        },
    )
    api.request("PUT", "gps", {"source": "gpsd", "serial_port": "", "baud_rate": 0, "gpsd_host": "localhost", "gpsd_port": 2947})
    api.request("PUT", "agw", {"listen_addr": "0.0.0.0:8000", "callsigns": args.callsign, "enabled": False})
    kiss = api.request(
        "POST",
        "kiss",
        {
            "type": "tcp",
            "tcp_port": 8001,
            "local_only": False,
            "serial_device": "",
            "baud_rate": 0,
            "channel": channel_id,
            "mode": "modem",
            "tnc_ingress_rate_hz": 50,
            "tnc_ingress_burst": 100,
            "allow_tx_from_governor": False,
            "gate_tx_to_is": False,
            "allow_connected_mode": False,
            "enabled": False,
        },
    )
    api.request("PUT", "digipeater", {"enabled": False, "dedupe_window_seconds": 30, "my_call": args.callsign})
    rule_payload = {
        "from_channel": channel_id,
        "to_channel": channel_id,
        "alias": "WIDE",
        "alias_type": "widen",
        "max_hops": 1,
        "action": "repeat",
        "priority": 10,
        "enabled": False,
    }
    rule = api.request(
        "POST",
        "digipeater/rules",
        rule_payload,
    )
    api.request(
        "PUT",
        "igate/config",
        {
            "enabled": False,
            "server": args.igate_server,
            "port": 14580,
            "server_filter": "",
            "simulation_mode": True,
            "gate_rf_to_is": False,
            "gate_is_to_rf": False,
            "rf_channel": channel_id,
            "is_tx_via": "",
            "software_name": "graywolf",
            "software_version": "0.14.13",
            "tx_channel": channel_id,
        },
    )
    beacon_payload = {
            "type": "tracker",
            "channel": channel_id,
            "callsign": "",
            "destination": "APGRWO",
            "path": "",
            "use_gps": True,
            "latitude": 0,
            "longitude": 0,
            "alt_ft": 0,
            "ambiguity": 0,
            "symbol_table": "\\",
            "symbol": "&",
            "overlay": "T",
            "position_format": "uncompressed",
            "messaging": False,
            "comment": args.beacon_comment,
            "comment_cmd": "",
            "custom_info": "",
            "object_name": "",
            "power": 0,
            "height": 0,
            "gain": 0,
            "dir": 0,
            "freq": "",
            "tone": "",
            "freq_offset": "",
            "delay_seconds": 30,
            "interval": 1800,
            "slot_seconds": -1,
            "smart_beacon": False,
            "sb_fast_speed": 0,
            "sb_slow_speed": 0,
            "sb_fast_rate": 0,
            "sb_slow_rate": 0,
            "sb_turn_angle": 0,
            "sb_turn_slope": 0,
            "sb_min_turn_time": 0,
            "send_path": "both",
            "enabled": False,
    }
    beacon = api.request("POST", "beacons", beacon_payload)
    # Graywolf's SQLite create defaults currently turn false boolean values on
    # and replace an empty beacon path. Full-resource PUTs must immediately
    # restore the requested safe values before read-back validation.
    api.request("PUT", f"digipeater/rules/{int(rule['id'])}", rule_payload)
    api.request("PUT", f"beacons/{int(beacon['id'])}", beacon_payload)
    return {
        "input_device_id": input_id,
        "output_device_id": output_id,
        "channel_id": channel_id,
        "kiss_id": int(kiss["id"]),
        "digipeater_rule_id": int(rule["id"]),
        "beacon_id": int(beacon["id"]),
    }


def verify_staged(api: Api, callsign: str) -> None:
    station = api.request("GET", "station/config")
    channels = api.request("GET", "channels")
    beacons = api.request("GET", "beacons")
    rules = api.request("GET", "digipeater/rules")
    kiss = api.request("GET", "kiss")
    digipeater = api.request("GET", "digipeater")
    igate = api.request("GET", "igate/config")
    agw = api.request("GET", "agw")
    if station != {"callsign": callsign}:
        raise RuntimeError("station callsign read-back mismatch")
    if len(channels) != 1 or channels[0].get("enabled") is not False:
        raise RuntimeError("staged channel is missing or enabled")
    if len(beacons) != 1 or beacons[0].get("enabled") is not False:
        raise RuntimeError("staged beacon is missing or enabled")
    if beacons[0].get("path") != "":
        raise RuntimeError("staged beacon is not configured for direct transmission")
    if len(rules) != 1 or rules[0].get("enabled") is not False:
        raise RuntimeError("staged digipeater rule is missing or enabled")
    if len(kiss) != 1 or kiss[0].get("enabled") is not False:
        raise RuntimeError("staged KISS interface is missing or enabled")
    if digipeater.get("enabled") is not False:
        raise RuntimeError("staged digipeater is enabled")
    if (
        igate.get("enabled") is not False
        or igate.get("simulation_mode") is not True
        or igate.get("gate_rf_to_is") is not False
        or igate.get("gate_is_to_rf") is not False
    ):
        raise RuntimeError("staged iGate is not disabled in simulation mode")
    if agw.get("enabled") is not False:
        raise RuntimeError("staged AGW interface is enabled")


def verify_audio_devices(api: Api, input_path: str, output_path: str) -> None:
    devices = api.request("GET", "audio-devices")
    if not isinstance(devices, list) or len(devices) != 2:
        raise RuntimeError("Graywolf profile requires exactly two audio devices")
    paths = {device.get("direction"): device.get("device_path") for device in devices}
    if paths.get("input") != input_path:
        raise RuntimeError("Graywolf input audio path read-back mismatch")
    if paths.get("output") != output_path:
        raise RuntimeError("Graywolf output audio path read-back mismatch")


def repair_staged_defaults(api: Api, callsign: str) -> None:
    rules = api.request("GET", "digipeater/rules")
    beacons = api.request("GET", "beacons")
    if not isinstance(rules, list) or len(rules) != 1:
        raise RuntimeError("repair requires exactly one staged digipeater rule")
    if not isinstance(beacons, list) or len(beacons) != 1:
        raise RuntimeError("repair requires exactly one staged beacon")
    rule = dict(rules[0])
    rule_id = int(rule.pop("id"))
    rule["enabled"] = False
    api.request("PUT", f"digipeater/rules/{rule_id}", rule)
    beacon = dict(beacons[0])
    beacon_id = int(beacon.pop("id"))
    beacon["enabled"] = False
    beacon["path"] = ""
    api.request("PUT", f"beacons/{beacon_id}", beacon)
    api.request("PUT", "digipeater", {"enabled": False, "dedupe_window_seconds": 30, "my_call": callsign})


def resource_payload(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"Graywolf returned an invalid {name} resource")
    payload = dict(value)
    payload.pop("id", None)
    return payload


def set_active(api: Api, active: bool, igate_mode: str = "two-way") -> None:
    channels = api.request("GET", "channels")
    beacons = api.request("GET", "beacons")
    rules = api.request("GET", "digipeater/rules")
    kiss = api.request("GET", "kiss")
    if not all(isinstance(value, list) and len(value) == 1 for value in (channels, beacons, rules, kiss)):
        raise RuntimeError("activation requires exactly one channel, beacon, digipeater rule, and KISS interface")

    channel_id = int(channels[0]["id"])
    beacon_id = int(beacons[0]["id"])
    rule_id = int(rules[0]["id"])
    kiss_id = int(kiss[0]["id"])

    if not active:
        beacon = resource_payload(beacons[0], "beacon")
        beacon["enabled"] = False
        api.request("PUT", f"beacons/{beacon_id}", beacon)
        igate = resource_payload(api.request("GET", "igate/config"), "iGate")
        igate.update({
            "enabled": False,
            "simulation_mode": True,
            "gate_rf_to_is": False,
            "gate_is_to_rf": False,
        })
        api.request("PUT", "igate/config", igate)
        digipeater = resource_payload(api.request("GET", "digipeater"), "digipeater")
        digipeater["enabled"] = False
        api.request("PUT", "digipeater", digipeater)
        rule = resource_payload(rules[0], "digipeater rule")
        rule["enabled"] = False
        api.request("PUT", f"digipeater/rules/{rule_id}", rule)
        agw = resource_payload(api.request("GET", "agw"), "AGW")
        agw["enabled"] = False
        api.request("PUT", "agw", agw)
        api.request("PUT", f"kiss/{kiss_id}/enabled", {"enabled": False})
        api.request("PUT", f"channels/{channel_id}/enabled", {"enabled": False})
        return

    verify_staged(api, str(api.request("GET", "station/config")["callsign"]))
    api.request("PUT", f"channels/{channel_id}/enabled", {"enabled": True})
    api.request("PUT", f"kiss/{kiss_id}/enabled", {"enabled": True})
    agw = resource_payload(api.request("GET", "agw"), "AGW")
    agw["enabled"] = True
    api.request("PUT", "agw", agw)
    rule = resource_payload(rules[0], "digipeater rule")
    rule["enabled"] = True
    api.request("PUT", f"digipeater/rules/{rule_id}", rule)
    digipeater = resource_payload(api.request("GET", "digipeater"), "digipeater")
    digipeater["enabled"] = True
    api.request("PUT", "digipeater", digipeater)
    igate_enabled = igate_mode == "two-way"
    igate = resource_payload(api.request("GET", "igate/config"), "iGate")
    igate.update({
        "enabled": igate_enabled,
        "simulation_mode": not igate_enabled,
        "gate_rf_to_is": igate_enabled,
        "gate_is_to_rf": igate_enabled,
    })
    api.request("PUT", "igate/config", igate)
    beacon = resource_payload(beacons[0], "beacon")
    beacon.update({
        "enabled": True,
        "path": "",
        "send_path": "both" if igate_enabled else "rf",
    })
    api.request("PUT", f"beacons/{beacon_id}", beacon)


def verify_active(api: Api, callsign: str, igate_mode: str = "two-way") -> None:
    station = api.request("GET", "station/config")
    channels = api.request("GET", "channels")
    beacons = api.request("GET", "beacons")
    rules = api.request("GET", "digipeater/rules")
    kiss = api.request("GET", "kiss")
    digipeater = api.request("GET", "digipeater")
    igate = api.request("GET", "igate/config")
    agw = api.request("GET", "agw")
    if station != {"callsign": callsign}:
        raise RuntimeError("active station callsign mismatch")
    igate_enabled = igate_mode == "two-way"
    checks = (
        (len(channels) == 1 and channels[0].get("enabled") is True, "channel"),
        (len(beacons) == 1 and beacons[0].get("enabled") is True and beacons[0].get("path") == "" and beacons[0].get("send_path") == ("both" if igate_enabled else "rf"), "beacon"),
        (len(rules) == 1 and rules[0].get("enabled") is True, "digipeater rule"),
        (len(kiss) == 1 and kiss[0].get("enabled") is True, "KISS"),
        (isinstance(digipeater, dict) and digipeater.get("enabled") is True, "digipeater"),
        (
            isinstance(igate, dict)
            and igate.get("enabled") is igate_enabled
            and igate.get("simulation_mode") is (not igate_enabled)
            and igate.get("gate_rf_to_is") is igate_enabled
            and igate.get("gate_is_to_rf") is igate_enabled,
            "iGate",
        ),
        (isinstance(agw, dict) and agw.get("enabled") is True, "AGW"),
    )
    failed = [name for okay, name in checks if not okay]
    if failed:
        raise RuntimeError("active Graywolf profile mismatch: " + ", ".join(failed))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("stage", "repair", "verify", "activate", "deactivate", "verify-active"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8071")
    parser.add_argument("--credential-file", type=Path, default=Path("/etc/pcs/aprs/graywolf-admin.json"))
    parser.add_argument("--callsign", required=True)
    parser.add_argument(
        "--audio-input-device",
        default="hw:CARD=Device,DEV=0",
        help="capture PCM; PCS uses the native endpoint to avoid CPAL/ALSA POLLERR loops",
    )
    parser.add_argument("--audio-output-device", default="plughw:CARD=Device,DEV=0")
    parser.add_argument(
        "--audio-device",
        help="deprecated compatibility option that sets both input and output PCMs",
    )
    parser.add_argument("--gpio-line", type=int, default=6)
    parser.add_argument("--tx-delay-ms", type=int, default=700)
    parser.add_argument("--tx-tail-ms", type=int, default=200)
    parser.add_argument("--igate-server", default="noam.aprs2.net")
    parser.add_argument("--igate-mode", choices=("disabled", "two-way"), default="two-way")
    parser.add_argument("--beacon-comment", default="Portable Comm Server - Local APRS Fill In Hotspot")
    args = parser.parse_args()
    if args.audio_device:
        args.audio_input_device = args.audio_device
        args.audio_output_device = args.audio_device
    return args


def main() -> int:
    args = parse_args()
    api = Api(args.base_url)
    load_or_create_credentials(api, args.credential_file)
    if args.command == "stage":
        ids = stage_profile(api, args)
        verify_staged(api, args.callsign)
        verify_audio_devices(api, args.audio_input_device, args.audio_output_device)
        print("Graywolf equivalent profile staged disabled; identifiers: " + json.dumps(ids, sort_keys=True))
    elif args.command == "repair":
        repair_staged_defaults(api, args.callsign)
        verify_staged(api, args.callsign)
        verify_audio_devices(api, args.audio_input_device, args.audio_output_device)
        print("Graywolf staged profile defaults repaired and read-back verified.")
    elif args.command == "verify":
        verify_staged(api, args.callsign)
        verify_audio_devices(api, args.audio_input_device, args.audio_output_device)
        print("Graywolf staged profile read-back is safe and complete.")
    elif args.command == "activate":
        set_active(api, True, args.igate_mode)
        verify_active(api, args.callsign, args.igate_mode)
        verify_audio_devices(api, args.audio_input_device, args.audio_output_device)
        print("Graywolf production profile activated and read-back verified.")
    elif args.command == "deactivate":
        set_active(api, False)
        verify_staged(api, args.callsign)
        verify_audio_devices(api, args.audio_input_device, args.audio_output_device)
        print("Graywolf production profile deactivated to safe staged state.")
    else:
        verify_active(api, args.callsign, args.igate_mode)
        verify_audio_devices(api, args.audio_input_device, args.audio_output_device)
        print("Graywolf production profile read-back is active and complete.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
