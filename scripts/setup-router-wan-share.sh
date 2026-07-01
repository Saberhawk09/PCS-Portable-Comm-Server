#!/usr/bin/env bash

set -Eeuo pipefail

CONNECTION_NAME="pcs-router-wan-share"
INTERFACE_NAME="eth0"
ROUTER_NET="10.42.0.1/24"

echo
echo "=== PCS Router WAN Share Setup ==="
echo

if ! command -v nmcli >/dev/null 2>&1; then
    echo "ERROR: nmcli not found. NetworkManager is required."
    exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
    ${SUDO} -v
fi

echo "This will create a NetworkManager shared Ethernet profile."
echo
echo "Purpose:"
echo "  Share the Pi's current internet connection out through ${INTERFACE_NAME}"
echo "  so a router WAN port can receive an IP address from the Pi."
echo
echo "Planned profile:"
echo "  Name:      ${CONNECTION_NAME}"
echo "  Interface: ${INTERFACE_NAME}"
echo "  Address:   ${ROUTER_NET}"
echo
echo "This does not modify Wi-Fi credentials or cellular settings."
echo

read -r -p "Continue? [y/N] " answer

case "${answer}" in
    y|Y|yes|YES)
        ;;
    *)
        echo "Aborted."
        exit 0
        ;;
esac

echo
echo "Current NetworkManager devices:"
nmcli device status
echo

if nmcli connection show "${CONNECTION_NAME}" >/dev/null 2>&1; then
    echo "Existing ${CONNECTION_NAME} profile found. Updating it..."
    ${SUDO} nmcli connection modify "${CONNECTION_NAME}" \
        connection.interface-name "${INTERFACE_NAME}" \
        ipv4.method shared \
        ipv4.addresses "${ROUTER_NET}" \
        ipv6.method ignore \
        connection.autoconnect yes
else
    echo "Creating ${CONNECTION_NAME} profile..."
    ${SUDO} nmcli connection add \
        type ethernet \
        ifname "${INTERFACE_NAME}" \
        con-name "${CONNECTION_NAME}" \
        ipv4.method shared \
        ipv4.addresses "${ROUTER_NET}" \
        ipv6.method ignore \
        connection.autoconnect yes
fi

echo
echo "PCS router WAN share profile configured."
echo
echo "To activate after plugging router WAN into Pi eth0:"
echo "  sudo nmcli connection up ${CONNECTION_NAME}"
echo
echo "To disable:"
echo "  sudo nmcli connection down ${CONNECTION_NAME}"
echo
echo "To delete this profile:"
echo "  sudo nmcli connection delete ${CONNECTION_NAME}"
echo
echo "Expected result:"
echo "  Router WAN receives a 10.42.0.x address from the Pi."
echo "  Router clients may reach the internet through the Pi's current uplink."
echo
