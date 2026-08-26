#!/usr/bin/env bash

set -Eeuo pipefail

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export PATH

CONFIG_FILE="${PCS_WIREGUARD_CONFIG:-/etc/pcs/wireguard-management.conf}"

if [[ ! -r "${CONFIG_FILE}" ]]; then
    echo "ERROR: WireGuard management config is not readable: ${CONFIG_FILE}" >&2
    exit 1
fi

# shellcheck source=/dev/null
source "${CONFIG_FILE}"

interface="${PCS_WG_INTERFACE:-wg-pcs}"
endpoint="${PCS_WG_ENDPOINT:-}"
peer_key="${PCS_WG_HUB_PUBLIC_KEY:-}"

if [[ "${interface}" != "wg-pcs" || -z "${endpoint}" || -z "${peer_key}" ]]; then
    echo "ERROR: WireGuard endpoint refresh configuration is incomplete." >&2
    exit 1
fi
if [[ "${endpoint}" != *:* ]]; then
    echo "ERROR: WireGuard endpoint must use hostname-or-IPv4:port syntax." >&2
    exit 1
fi

host="${endpoint%:*}"
port="${endpoint##*:}"
if [[ -z "${host}" || ! "${port}" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
    echo "ERROR: WireGuard endpoint is invalid." >&2
    exit 1
fi

ipv4="$(python3 - "${host}" <<'PY'
import ipaddress
import socket
import sys

host = sys.argv[1]
try:
    print(ipaddress.IPv4Address(host))
except ipaddress.AddressValueError:
    addresses = []
    for result in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM):
        address = result[4][0]
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise SystemExit("ERROR: endpoint hostname has no IPv4 address")
    print(addresses[0])
PY
)"

if ! wg show "${interface}" peers | grep -Fxq "${peer_key}"; then
    echo "ERROR: configured WireGuard peer is not active on ${interface}." >&2
    exit 1
fi

wg set "${interface}" peer "${peer_key}" endpoint "${ipv4}:${port}"
echo "PCS WireGuard endpoint refreshed from the configured hostname using IPv4."
