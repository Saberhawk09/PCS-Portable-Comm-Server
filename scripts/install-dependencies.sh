#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_NAME="PCS - Portable Communication Server"

echo
echo "=== ${PROJECT_NAME} dependency installer ==="
echo

if ! command -v apt-get >/dev/null 2>&1; then
    echo "ERROR: This installer expects a Debian/Raspberry Pi OS system using apt."
    exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
    ${SUDO} -v
fi

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    echo "Detected OS: ${PRETTY_NAME:-unknown}"
fi

if [[ -f /proc/device-tree/model ]]; then
    echo -n "Detected hardware: "
    tr -d '\0' < /proc/device-tree/model
    echo
fi

echo
echo "This script installs PCS baseline dependencies."
echo "It does not configure routing, Samba shares, GPSD, Chrony sources, or cellular profiles."
echo

BASE_PACKAGES=(
    ca-certificates
    curl
    wget
    git
    openssh-client
    nano
    vim
    htop
    tree
    lsof
    jq
)

HARDWARE_PACKAGES=(
    i2c-tools
    python3-gpiozero
    python3-spidev
    usbutils
    pciutils
    util-linux-extra
    rfkill
    ethtool
)

NETWORK_PACKAGES=(
    network-manager
    modemmanager
    usb-modeswitch
    libqmi-utils
    libmbim-utils
    iproute2
    iputils-ping
    dnsutils
    traceroute
    net-tools
    avahi-daemon
)

SERVER_PACKAGES=(
    samba
    smbclient
    gpsd
    gpsd-clients
    pps-tools
    chrony
    cockpit
)

FUTURE_PACKAGES=(
    wireguard-tools
)

ALL_PACKAGES=(
    "${BASE_PACKAGES[@]}"
    "${HARDWARE_PACKAGES[@]}"
    "${NETWORK_PACKAGES[@]}"
    "${SERVER_PACKAGES[@]}"
    "${FUTURE_PACKAGES[@]}"
)

echo
echo "Updating package lists..."
${SUDO} apt-get update

echo
echo "Installing packages..."
${SUDO} env DEBIAN_FRONTEND=noninteractive apt-get install -y "${ALL_PACKAGES[@]}"

echo
echo "Enabling useful baseline services where available..."
if [[ "${PCS_DEFER_MODEMMANAGER_START:-0}" == "1" ]]; then
    echo "ModemManager startup will be deferred so WWAN setup can soft-replug USB before modem detection."
fi

SERVICES_TO_ENABLE=(
    NetworkManager.service
    ModemManager.service
    avahi-daemon.service
    chrony.service
    cockpit.socket
)

for service in "${SERVICES_TO_ENABLE[@]}"; do
    if systemctl list-unit-files --no-legend "${service}" 2>/dev/null | awk '{print $1}' | grep -qx "${service}"; then
        if [[ "${service}" == "ModemManager.service" && "${PCS_DEFER_MODEMMANAGER_START:-0}" == "1" ]]; then
            echo "Enabling ${service} without starting it yet..."
            ${SUDO} systemctl enable "${service}" || true
            ${SUDO} systemctl stop "${service}" 2>/dev/null || true
        else
            echo "Enabling ${service}..."
            ${SUDO} systemctl enable --now "${service}" || true
        fi
    else
        echo "Skipping ${service}; unit not found."
    fi
done

echo
echo "Installed package groups:"
echo "- Base tools"
echo "- Hardware inspection tools"
echo "- Network and modem tools"
echo "- Samba / SMB tools"
echo "- GPSD tools"
echo "- Chrony"
echo "- Cockpit"
echo "- WireGuard tools"

echo
echo "PCS dependency installation complete."
echo
echo "Suggested next checks:"
echo "  systemctl status ModemManager.service --no-pager"
echo "  systemctl status chrony.service --no-pager"
echo "  systemctl status cockpit.socket --no-pager"
echo "  nmcli device status"
echo "  lsusb"
echo
echo "Cockpit should be available at:"
echo "  https://pcs-pi.local:9090"
echo "or:"
echo "  https://PI_IP_ADDRESS:9090"
echo
