#!/usr/bin/env bash

set -Eeuo pipefail

CONNECTION_NAME="pcs-router-wan-share"
INTERFACE_NAME="eth0"
ROUTER_NET="10.42.0.1/24"
AUTOCONNECT_PRIORITY="100"
DISABLED_AUTOCONNECT_PRIORITY="-100"

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

if [[ "${PCS_ASSUME_YES:-}" == "1" || "${PCS_ROUTER_WAN_SHARE_CONFIRM:-}" == "yes" ]]; then
    answer="yes"
    echo "Continue? [Y/N] yes"
else
    read -r -p "Continue? [Y/N] " answer
fi

case "${answer}" in
    y|Y|yes|YES|Yes)
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

disable_competing_eth0_profiles() {
    echo "Checking for competing NetworkManager profiles on ${INTERFACE_NAME}..."

    while IFS=: read -r name type device; do
        [[ -z "${name}" ]] && continue
        [[ "${name}" == "${CONNECTION_NAME}" ]] && continue
        [[ "${type}" == "802-3-ethernet" || "${type}" == "ethernet" ]] || continue

        interface_name="$(nmcli -g connection.interface-name connection show "${name}" 2>/dev/null || true)"

        if [[ "${device}" == "${INTERFACE_NAME}" || "${interface_name}" == "${INTERFACE_NAME}" || "${name}" == "netplan-${INTERFACE_NAME}" ]]; then
            echo "Disabling competing ${INTERFACE_NAME} profile: ${name}"
            ${SUDO} nmcli connection modify "${name}" \
                connection.autoconnect no \
                connection.autoconnect-priority "${DISABLED_AUTOCONNECT_PRIORITY}" || true

            if nmcli -t -f NAME,DEVICE connection show --active | grep -Fxq "${name}:${INTERFACE_NAME}"; then
                ${SUDO} nmcli connection down "${name}" || true
            fi
        fi
    done < <(nmcli -t -f NAME,TYPE,DEVICE connection show)
}

if nmcli connection show "${CONNECTION_NAME}" >/dev/null 2>&1; then
    echo "Existing ${CONNECTION_NAME} profile found. Updating it..."
    ${SUDO} nmcli connection modify "${CONNECTION_NAME}" \
        connection.interface-name "${INTERFACE_NAME}" \
        ipv4.method shared \
        ipv4.addresses "${ROUTER_NET}" \
        ipv6.method ignore \
        connection.autoconnect yes \
        connection.autoconnect-priority "${AUTOCONNECT_PRIORITY}"
else
    echo "Creating ${CONNECTION_NAME} profile..."
    ${SUDO} nmcli connection add \
        type ethernet \
        ifname "${INTERFACE_NAME}" \
        con-name "${CONNECTION_NAME}" \
        ipv4.method shared \
        ipv4.addresses "${ROUTER_NET}" \
        ipv6.method ignore \
        connection.autoconnect yes \
        connection.autoconnect-priority "${AUTOCONNECT_PRIORITY}"
fi

disable_competing_eth0_profiles

echo
echo "Activating ${CONNECTION_NAME} on ${INTERFACE_NAME}..."
if ${SUDO} nmcli connection up "${CONNECTION_NAME}"; then
    echo "${CONNECTION_NAME} is active."
else
    echo "WARNING: ${CONNECTION_NAME} could not be activated right now."
    echo "It should autoconnect when ${INTERFACE_NAME} has link."
fi

echo
echo "PCS router WAN share profile configured."
echo
echo "To reactivate after plugging router WAN into Pi eth0:"
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
