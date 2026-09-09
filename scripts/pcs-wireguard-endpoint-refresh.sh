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

if ! wg show "${interface}" peers | grep -Fxq "${peer_key}"; then
    echo "ERROR: configured WireGuard peer is not active on ${interface}." >&2
    exit 1
fi

resolve_status=0
ipv4="$(python3 - "${host}" <<'PY'
import ipaddress
import signal
import socket
import sys
import time

def deferred(*_):
    print("WARNING: DNS temporarily unavailable; retaining the current WireGuard endpoint until the next timer retry.", file=sys.stderr)
    raise SystemExit(75)

signal.signal(signal.SIGALRM, deferred)
signal.alarm(20)

host = sys.argv[1]
try:
    print(ipaddress.IPv4Address(host))
except ipaddress.AddressValueError:
    addresses = []
    for attempt in range(3):
        try:
            results = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
            break
        except socket.gaierror as exc:
            if exc.errno != socket.EAI_AGAIN:
                raise SystemExit("ERROR: endpoint DNS lookup failed permanently") from None
            if attempt == 2:
                deferred()
            time.sleep(2)
    for result in results:
        address = result[4][0]
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise SystemExit("ERROR: endpoint hostname has no IPv4 address")
    print(addresses[0])
PY
)" || resolve_status=$?

if (( resolve_status != 0 )); then
    exit "${resolve_status}"
fi

wg set "${interface}" peer "${peer_key}" endpoint "${ipv4}:${port}"
echo "PCS WireGuard endpoint refreshed from the configured hostname using IPv4."
