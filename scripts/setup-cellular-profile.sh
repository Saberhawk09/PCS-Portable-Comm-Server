#!/usr/bin/env bash

set -Eeuo pipefail

CONNECTION_NAME="pcs-cellular-tmobile"
CELLULAR_APN="fast.t-mobile.com"
ROUTE_METRIC="900"

echo
echo "=== PCS Cellular Profile Setup ==="
echo

if ! command -v nmcli >/dev/null 2>&1; then
    echo "ERROR: nmcli not found. NetworkManager is required."
    exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

echo "Configuring manual cellular profile:"
echo "  Name: ${CONNECTION_NAME}"
echo "  APN:  ${CELLULAR_APN}"
echo

if nmcli -t -f NAME connection show | grep -qx "${CONNECTION_NAME}"; then
    echo "Existing ${CONNECTION_NAME} profile found. Updating it..."
else
    echo "Creating ${CONNECTION_NAME} profile..."
    ${SUDO} nmcli connection add \
        type gsm \
        ifname "*" \
        con-name "${CONNECTION_NAME}" \
        apn "${CELLULAR_APN}"
fi

${SUDO} nmcli connection modify "${CONNECTION_NAME}" \
    gsm.apn "${CELLULAR_APN}" \
    connection.autoconnect no \
    ipv4.method auto \
    ipv6.method auto \
    ipv4.route-metric "${ROUTE_METRIC}" \
    ipv6.route-metric "${ROUTE_METRIC}"

echo
echo "PCS cellular profile configured for manual control."
echo "It will not autoconnect on boot."
echo
echo "Use the PCS Control Panel to connect or disconnect cellular data."
