#!/usr/bin/env python3
"""Validate and import a constrained wg-quick client profile for PCS."""

from __future__ import annotations

import argparse
import base64
import binascii
import configparser
import ipaddress
import os
import re
import shlex
from pathlib import Path


PCS_LAN = ipaddress.IPv4Network("10.42.0.0/24")
INTERFACE_KEYS = {"Address", "PrivateKey", "DNS"}
PEER_KEYS = {
    "PublicKey",
    "PresharedKey",
    "Endpoint",
    "AllowedIPs",
    "PersistentKeepalive",
}


class ProfileError(ValueError):
    """Raised when a profile is unsafe or incompatible with PCS policy."""


def _wireguard_key(value: str, label: str) -> str:
    value = value.strip()
    if not value or value == "CHANGE_ME":
        raise ProfileError(f"{label} is missing")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProfileError(f"{label} is not valid base64") from exc
    if len(decoded) != 32:
        raise ProfileError(f"{label} must decode to 32 bytes")
    return value


def _exact_keys(section: configparser.SectionProxy, allowed: set[str], label: str) -> None:
    actual = set(section.keys())
    unexpected = sorted(actual - allowed)
    if unexpected:
        raise ProfileError(
            f"unsupported {label} option(s): {', '.join(unexpected)}; "
            "routing hooks, Table, MTU, and other wg-quick extensions are not imported"
        )


def parse_profile(path: Path | str) -> dict[str, str]:
    profile = Path(path)
    parser = configparser.RawConfigParser(
        interpolation=None,
        strict=True,
        empty_lines_in_values=False,
    )
    parser.optionxform = str
    try:
        with profile.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise ProfileError(f"could not parse WireGuard profile: {exc}") from exc

    if parser.sections() != ["Interface", "Peer"]:
        raise ProfileError("profile must contain exactly one [Interface] followed by one [Peer]")

    interface = parser["Interface"]
    peer = parser["Peer"]
    _exact_keys(interface, INTERFACE_KEYS, "[Interface]")
    _exact_keys(peer, PEER_KEYS, "[Peer]")

    required_interface = {"Address", "PrivateKey"}
    missing_interface = sorted(required_interface - set(interface.keys()))
    required_peer = {"PublicKey", "Endpoint", "AllowedIPs"}
    missing_peer = sorted(required_peer - set(peer.keys()))
    if missing_interface or missing_peer:
        missing = [f"[Interface] {key}" for key in missing_interface]
        missing.extend(f"[Peer] {key}" for key in missing_peer)
        raise ProfileError(f"missing required option(s): {', '.join(missing)}")

    address_text = interface["Address"].strip()
    if "," in address_text:
        raise ProfileError("Address must contain exactly one IPv4 /32 address")
    try:
        address = ipaddress.ip_interface(address_text)
    except ValueError as exc:
        raise ProfileError("Address must be a valid IPv4 /32 address") from exc
    if not isinstance(address, ipaddress.IPv4Interface) or address.network.prefixlen != 32:
        raise ProfileError("Address must contain exactly one IPv4 /32 address")

    management_network = ipaddress.IPv4Network(f"{address.ip}/24", strict=False)
    if management_network.overlaps(PCS_LAN):
        raise ProfileError("WireGuard management /24 must not overlap the PCS LAN 10.42.0.0/24")

    ignored_dns = interface.get("DNS", "").strip()
    if ignored_dns:
        try:
            dns_address = ipaddress.ip_address(ignored_dns)
        except ValueError as exc:
            raise ProfileError("DNS must be one IPv4 address when present") from exc
        if not isinstance(dns_address, ipaddress.IPv4Address):
            raise ProfileError("DNS must be one IPv4 address when present")
        if dns_address not in management_network or dns_address == address.ip:
            raise ProfileError("DNS must be another address in the WireGuard management /24")

    allowed_routes: list[str] = []
    seen_routes: set[str] = set()
    for item in peer["AllowedIPs"].split(","):
        route_text = item.strip()
        if not route_text:
            raise ProfileError("AllowedIPs contains an empty route")
        try:
            route = ipaddress.ip_network(route_text, strict=True)
        except ValueError as exc:
            raise ProfileError(f"AllowedIPs entry is invalid: {route_text}") from exc
        if not isinstance(route, ipaddress.IPv4Network) or route.prefixlen != 32:
            raise ProfileError("AllowedIPs entries must be explicit IPv4 /32 host routes")
        if not route.subnet_of(management_network):
            raise ProfileError(
                f"AllowedIPs entry {route} is outside the PCS WireGuard management /24"
            )
        if address.ip in route:
            raise ProfileError("AllowedIPs must not route the PCS WireGuard address to its peer")
        normalized = str(route)
        if normalized not in seen_routes:
            seen_routes.add(normalized)
            allowed_routes.append(normalized)
    if not allowed_routes:
        raise ProfileError("AllowedIPs must contain at least one management /32")

    endpoint = peer["Endpoint"].strip()
    if endpoint.count(":") != 1:
        raise ProfileError("Endpoint must use hostname-or-IPv4:port syntax")
    host, port_text = endpoint.rsplit(":", 1)
    if (
        not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host)
        or ".." in host
        or host.startswith((".", "-"))
        or host.endswith((".", "-"))
        or not port_text.isdigit()
        or not 1 <= int(port_text) <= 65535
    ):
        raise ProfileError("Endpoint is invalid")

    keepalive = peer.get("PersistentKeepalive", "25").strip()
    if not keepalive.isdigit() or not 1 <= int(keepalive) <= 65535:
        raise ProfileError("PersistentKeepalive must be 1-65535 seconds")

    return {
        "address": str(address),
        "private_key": _wireguard_key(interface["PrivateKey"], "PrivateKey"),
        "hub_public_key": _wireguard_key(peer["PublicKey"], "PublicKey"),
        "preshared_key": (
            _wireguard_key(peer["PresharedKey"], "PresharedKey")
            if peer.get("PresharedKey", "").strip()
            else ""
        ),
        "endpoint": endpoint,
        "allowed_ips": ",".join(allowed_routes),
        # A supplied profile has one peer. Every route to that peer is therefore
        # an explicitly approved management source in the PCS firewall policy.
        "admin_sources": ",".join(allowed_routes),
        "persistent_keepalive": keepalive,
        # ASUS exports a tunnel DNS address by default. PCS deliberately keeps
        # its existing DNS policy, so this validated value is never rendered.
        "ignored_dns": ignored_dns,
    }


def write_import_files(
    profile: dict[str, str],
    policy_path: Path,
    key_path: Path,
    preshared_key_path: Path,
) -> None:
    use_preshared_key = "yes" if profile["preshared_key"] else "no"
    values = {
        "PCS_WG_ADDRESS": profile["address"],
        "PCS_WG_HUB_PUBLIC_KEY": profile["hub_public_key"],
        "PCS_WG_ENDPOINT": profile["endpoint"],
        "PCS_WG_ALLOWED_IPS": profile["allowed_ips"],
        "PCS_WG_ADMIN_SOURCES": profile["admin_sources"],
        "PCS_WG_PERSISTENT_KEEPALIVE": profile["persistent_keepalive"],
        "PCS_WG_INTERFACE": "wg-pcs",
        "PCS_WG_LAN_INTERFACE": "eth0",
        "PCS_WG_LAN_NETWORK": "10.42.0.0/24",
        "PCS_WG_MTU": "1280",
        # Home-Wi-Fi trust is deployment-local and cannot be inferred safely
        # from a WireGuard export. It remains fail-closed until configured.
        "PCS_WG_HOME_INTERFACE": "",
        "PCS_WG_HOME_NETWORK": "",
        "PCS_WG_PROTECTED_TCP_PORTS": "22,80,139,443,445,8080,9090",
        "PCS_WG_PRIVATE_KEY_FILE": "/etc/pcs/wireguard/private.key",
        "PCS_WG_USE_PRESHARED_KEY": use_preshared_key,
        "PCS_WG_PRESHARED_KEY_FILE": "/etc/pcs/wireguard/preshared.key",
    }
    policy_path.write_text(
        "# Imported by setup-wireguard-management.sh; contains no private key.\n"
        + "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    key_path.write_text(f"{profile['private_key']}\n", encoding="ascii")
    preshared_key_path.write_text(
        f"{profile['preshared_key']}\n" if profile["preshared_key"] else "",
        encoding="ascii",
    )
    os.chmod(policy_path, 0o600)
    os.chmod(key_path, 0o600)
    os.chmod(preshared_key_path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a restricted PCS WireGuard wg-quick profile without printing secrets."
    )
    parser.add_argument("--validate", metavar="PROFILE", type=Path)
    parser.add_argument(
        "--render",
        metavar=("PROFILE", "POLICY", "PRIVATE_KEY", "PRESHARED_KEY"),
        nargs=4,
        type=Path,
    )
    args = parser.parse_args()

    if bool(args.validate) == bool(args.render):
        parser.error(
            "choose exactly one of --validate PROFILE or "
            "--render PROFILE POLICY PRIVATE_KEY PRESHARED_KEY"
        )

    try:
        if args.validate:
            parse_profile(args.validate)
            print("PCS WireGuard profile is safe to import.")
        else:
            profile_path, policy_path, key_path, preshared_key_path = args.render
            write_import_files(
                parse_profile(profile_path), policy_path, key_path, preshared_key_path
            )
            print("PCS WireGuard profile was rendered to protected temporary files.")
    except ProfileError as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
