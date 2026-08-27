#!/usr/bin/env bash

set -Eeuo pipefail

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export PATH

CONFIG_FILE="${PCS_STATS_API_CONFIG:-/etc/pcs-stats-api/policy.conf}"
WIREGUARD_CONFIG="${PCS_WIREGUARD_CONFIG:-/etc/pcs/wireguard-management.conf}"
MODE="${1:---apply}"
TABLE="pcs_stats_api"

if [[ "${MODE}" == "--clear" || "${MODE}" == "-h" || "${MODE}" == "--help" ]]; then
    :
elif [[ -r "${CONFIG_FILE}" ]]; then
    # Runtime policy is installed root-owned and is validated before use.
    # shellcheck source=/dev/null
    source "${CONFIG_FILE}"
else
    echo "ERROR: PCS Stats API policy is not readable: ${CONFIG_FILE}" >&2
    exit 1
fi

API_PORT="${PCS_API_PORT:-}"
ALLOWED_SOURCES="${PCS_API_ALLOWED_INTERFACE_SOURCES:-}"

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "ERROR: $1 is required for the PCS Stats API firewall." >&2
        exit 1
    }
}

validate_config() {
    python3 - "${API_PORT}" "${ALLOWED_SOURCES}" "${WIREGUARD_CONFIG}" <<'PY'
import ipaddress
import re
import sys
from pathlib import Path

port_text, mappings_text, wg_config_path = sys.argv[1:]
wg_admin_text = ""
try:
    for raw_line in Path(wg_config_path).read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r'PCS_WG_ADMIN_SOURCES=(?:"([^"]*)"|\'([^\']*)\'|([^#\s]+))', raw_line.strip())
        if match:
            wg_admin_text = next(value for value in match.groups() if value is not None)
            break
except (OSError, UnicodeError):
    pass

if port_text != "9443":
    raise SystemExit("ERROR: PCS Stats API currently requires fixed TCP port 9443")

allowed_interfaces = {"eth0", "wg-pcs", "wlan0"}
pcs_lan = ipaddress.ip_network("10.42.0.0/24")
seen = set()
mappings = []
for raw in mappings_text.split(","):
    raw = raw.strip()
    if not raw or "=" not in raw:
        raise SystemExit("ERROR: every allowed source must use interface=IPv4-network")
    interface, network_text = (part.strip() for part in raw.split("=", 1))
    if interface not in allowed_interfaces or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", interface):
        raise SystemExit(f"ERROR: unsupported API source interface: {interface!r}")
    try:
        network = ipaddress.ip_network(network_text, strict=True)
    except ValueError as exc:
        raise SystemExit(f"ERROR: invalid API source network {network_text!r}: {exc}")
    if network.version != 4 or not network.is_private:
        raise SystemExit("ERROR: API source networks must be private IPv4 networks")
    if (interface, network) in seen:
        raise SystemExit("ERROR: duplicate API interface/source mapping")
    seen.add((interface, network))
    if interface == "eth0" and network != pcs_lan:
        raise SystemExit("ERROR: eth0 API access must be exactly 10.42.0.0/24")
    if interface == "wg-pcs" and network.prefixlen != 32:
        raise SystemExit("ERROR: WireGuard API sources must be explicit IPv4 /32s")
    if interface == "wlan0" and (network.prefixlen < 16 or network.overlaps(pcs_lan)):
        raise SystemExit("ERROR: trusted wlan0 sources must be /16 or narrower and not overlap the PCS LAN")
    mappings.append((interface, network))

if not any(interface == "eth0" and network == pcs_lan for interface, network in mappings):
    raise SystemExit("ERROR: API policy must retain PCS LAN access on eth0=10.42.0.0/24")

wg_mappings = {str(network) for interface, network in mappings if interface == "wg-pcs"}
if wg_admin_text:
    wg_admins = {item.strip() for item in wg_admin_text.split(",") if item.strip()}
    if not wg_mappings.issubset(wg_admins):
        raise SystemExit("ERROR: every WireGuard API source must be an approved PCS_WG_ADMIN_SOURCES entry")
PY
}

clear_rules() {
    nft delete table inet "${TABLE}" 2>/dev/null || true
}

apply_rules() {
    local mapping
    local interface
    local network

    clear_rules
    nft add table inet "${TABLE}"
    nft 'add chain inet pcs_stats_api input { type filter hook input priority -15; policy accept; }'
    nft add rule inet "${TABLE}" input iifname lo tcp dport "${API_PORT}" accept comment "pcs-api-loopback"
    IFS=',' read -r -a mappings <<<"${ALLOWED_SOURCES}"
    for mapping in "${mappings[@]}"; do
        interface="${mapping%%=*}"
        network="${mapping#*=}"
        interface="${interface//[[:space:]]/}"
        network="${network//[[:space:]]/}"
        nft add rule inet "${TABLE}" input iifname "${interface}" ip saddr "${network}" tcp dport "${API_PORT}" accept comment "pcs-api-allowed"
    done
    nft add rule inet "${TABLE}" input tcp dport "${API_PORT}" drop comment "pcs-api-default-deny"
    echo "PCS Stats API firewall is active on TCP ${API_PORT} for explicit trusted sources only."
}

check_rules() {
    local rules
    local mapping
    local interface
    local network
    local rendered_network

    rules="$(nft list table inet "${TABLE}")"
    grep -Fq "tcp dport ${API_PORT} drop" <<<"${rules}"
    grep -Fq 'comment "pcs-api-default-deny"' <<<"${rules}"
    IFS=',' read -r -a mappings <<<"${ALLOWED_SOURCES}"
    for mapping in "${mappings[@]}"; do
        interface="${mapping%%=*}"
        network="${mapping#*=}"
        interface="${interface//[[:space:]]/}"
        network="${network//[[:space:]]/}"
        rendered_network="${network%/32}"
        grep -F "iifname \"${interface}\"" <<<"${rules}" \
            | grep -F "ip saddr ${rendered_network}" \
            | grep -Fq "tcp dport ${API_PORT} accept"
    done
    echo "PCS Stats API firewall checks passed."
}

case "${MODE}" in
    --apply)
        require_command nft
        require_command python3
        validate_config
        apply_rules
        ;;
    --clear)
        require_command nft
        clear_rules
        ;;
    --check)
        require_command nft
        require_command python3
        validate_config
        check_rules
        ;;
    --validate-config)
        require_command python3
        validate_config
        echo "PCS Stats API firewall configuration is valid."
        ;;
    -h|--help)
        echo "Usage: pcs-stats-api-firewall [--apply|--clear|--check|--validate-config]"
        ;;
    *)
        echo "Usage: pcs-stats-api-firewall [--apply|--clear|--check|--validate-config]" >&2
        exit 2
        ;;
esac
