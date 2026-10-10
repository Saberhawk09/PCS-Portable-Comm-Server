#!/usr/bin/env bash


# The topology marker is written only by the supervised VLAN migration.
PCS_NETWORK_MODE=legacy
if [[ -e /etc/pcs/network-mode ]]; then
    IFS= read -r PCS_NETWORK_MODE </etc/pcs/network-mode || exit 2
fi
case "${PCS_NETWORK_MODE}" in
    legacy) PCS_LAN_INTERFACE=eth0; PCS_LAN_PROFILE=pcs-router-wan-share ;;
    vlan) PCS_LAN_INTERFACE=eth0.10; PCS_LAN_PROFILE=pcs-lan-vlan ;;
    *) echo "ERROR: invalid PCS network mode; refusing network operation" >&2; exit 2 ;;
esac


set -Eeuo pipefail

echo
echo "=== PCS Service Restart ==="
echo "Started: $(date)"
echo

SERVICES=(
    smbd
    chrony
    ModemManager
    avahi-daemon
    pcs-wsdd
)

for service in "${SERVICES[@]}"; do
    echo "--- ${service} ---"

    if systemctl list-unit-files | awk '{print $1}' | grep -qx "${service}.service"; then
        echo "Restarting ${service}..."
        systemctl restart "${service}"
        echo -n "Status after restart: "
        systemctl is-active "${service}" || true
    else
        echo "Skipping ${service}; service not found."
    fi

    echo
done

echo "--- PCS client LAN / AP handoff ---"
if command -v nmcli >/dev/null 2>&1; then
    if nmcli connection show "${PCS_LAN_PROFILE}" >/dev/null 2>&1; then
        echo "Reactivating ${PCS_LAN_PROFILE}..."
        nmcli connection up "${PCS_LAN_PROFILE}" || true
    else
        echo "${PCS_LAN_PROFILE} profile not found."
    fi
else
    echo "nmcli not found."
fi

echo
echo "Waiting 10 seconds for services to settle..."
sleep 10

echo
echo "--- PCS quick service status ---"
for service in smbd chrony ModemManager avahi-daemon pcs-wsdd cockpit.socket; do
    echo -n "${service}: "
    systemctl is-active "${service}" 2>/dev/null || true
done

echo
echo "--- Network status ---"
nmcli device status || true

echo
echo "--- Time status ---"
timedatectl | grep -E "System clock synchronized|NTP service|RTC in local TZ" || true

echo
echo "--- PCS LAN IP check ---"
ip -brief addr show "${PCS_LAN_INTERFACE}" || true

echo
echo "Finished: $(date)"
echo "=== PCS Service Restart Complete ==="
echo
echo "For a full validation test, run:"
echo "  cd /home/pi/Projects/PCS-Portable-Comm-Server"
echo "  ./scripts/pcs-self-test.sh"
echo
