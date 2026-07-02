#!/usr/bin/env bash

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

echo "--- Router WAN handoff ---"
if command -v nmcli >/dev/null 2>&1; then
    if nmcli connection show pcs-router-wan-share >/dev/null 2>&1; then
        echo "Reactivating pcs-router-wan-share..."
        nmcli connection up pcs-router-wan-share || true
    else
        echo "pcs-router-wan-share profile not found."
    fi
else
    echo "nmcli not found."
fi

echo
echo "Waiting 10 seconds for services to settle..."
sleep 10

echo
echo "--- PCS quick service status ---"
for service in smbd chrony ModemManager avahi-daemon cockpit.socket; do
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
echo "--- Router-side IP check ---"
ip -brief addr show eth0 || true

echo
echo "Finished: $(date)"
echo "=== PCS Service Restart Complete ==="
echo
echo "For a full validation test, run:"
echo "  cd /home/pi/Projects/PCS-Portable-Comm-Server"
echo "  ./scripts/pcs-self-test.sh"
echo
