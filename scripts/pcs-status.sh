#!/usr/bin/env bash

set -u

PCS_ETH_IFACE="eth0"
PCS_WIFI_IFACE="wlan0"
PCS_ROUTER_PROFILE="pcs-router-wan-share"
PCS_ROUTER_IP="10.42.0.1"
PCS_ROUTER_CIDR="10.42.0.1/24"

PCS_SHARE_NAME="PCS-Share"
PCS_SHARE_PATH="/mnt/pcs-usb/PCS-Share"

PCS_BACKUP_SHARE_NAME="PCS-Backup"
PCS_BACKUP_SHARE_PATH="/srv/pcs-share-backup"

PCS_HOSTNAME="$(hostname 2>/dev/null || echo pcs-pi)"

PCS_CONTROL_SERVICE="pcs-control-panel.service"
PCS_CONTROL_PORT="8080"
PCS_CONTROL_URL="http://10.42.0.1:8080"

echo "=== PCS System Status ==="
echo

echo "--- Hostname ---"
echo "${PCS_HOSTNAME}"
echo

echo "--- OS ---"
if [[ -f /etc/os-release ]]; then
    grep -E 'PRETTY_NAME|VERSION=' /etc/os-release
else
    echo "/etc/os-release not found"
fi
echo

echo "--- Hardware ---"
if [[ -f /proc/device-tree/model ]]; then
    tr -d '\0' < /proc/device-tree/model
    echo
else
    echo "Hardware model not found"
fi
echo

echo "--- Kernel ---"
uname -a
echo

echo "--- Uptime ---"
uptime
echo

echo "--- Time / NTP / RTC ---"
if command -v timedatectl >/dev/null 2>&1; then
    timedatectl
else
    echo "timedatectl not available"
fi
echo

echo "--- RTC Devices ---"
ls -l /dev/rtc* 2>/dev/null || echo "No RTC devices found"
echo

echo "--- Chrony Tracking ---"
if command -v chronyc >/dev/null 2>&1; then
    chronyc tracking 2>/dev/null || echo "chronyc tracking failed"
else
    echo "chronyc not available"
fi
echo

echo "--- NetworkManager Devices ---"
if command -v nmcli >/dev/null 2>&1; then
    nmcli device status
else
    echo "nmcli not available"
fi
echo

echo "--- Active Network Connections ---"
if command -v nmcli >/dev/null 2>&1; then
    timeout 8 nmcli --wait 5 connection show --active || echo "nmcli active connection check timed out or failed"
else
    echo "nmcli not available"
fi
echo

echo "--- IP Addresses ---"
ip -brief addr 2>/dev/null || ip addr
echo

echo "--- Routes ---"
ip route 2>/dev/null || echo "ip route unavailable"
echo

echo "--- DNS ---"
if command -v resolvectl >/dev/null 2>&1; then
    resolvectl status 2>/dev/null || cat /etc/resolv.conf
else
    cat /etc/resolv.conf
fi
echo

echo "--- Router-Side Neighbors on ${PCS_ETH_IFACE} ---"
ip neigh show dev "${PCS_ETH_IFACE}" 2>/dev/null || echo "No neighbor data for ${PCS_ETH_IFACE}"
echo

echo "--- USB Devices ---"
if command -v lsusb >/dev/null 2>&1; then
    lsusb
else
    echo "lsusb not available"
fi
echo

echo "--- I2C Bus 1 ---"
if command -v i2cdetect >/dev/null 2>&1; then
    sudo i2cdetect -y 1
else
    echo "i2c-tools not installed"
fi
echo

echo "--- ModemManager ---"
if command -v mmcli >/dev/null 2>&1; then
    mmcli -L || true
else
    echo "mmcli not installed"
fi
echo

echo "--- Samba Shares ---"
if command -v testparm >/dev/null 2>&1; then
    testparm -s 2>/dev/null | grep -E "^\[.*\]" || echo "No Samba shares found by testparm"
else
    echo "testparm not available"
fi
echo

echo "--- Samba Storage Paths ---"
for path in "${PCS_SHARE_PATH}" "${PCS_BACKUP_SHARE_PATH}"; do
    echo
    echo "[${path}]"
    if [[ -d "${path}" ]]; then
        df -h "${path}" || true
        ls -la "${path}" || true
    else
        echo "Missing"
    fi
done
echo

echo "--- Key Services ---"
for service in NetworkManager ModemManager avahi-daemon smbd gpsd chrony cockpit.socket pcs-control-panel.service; do
    echo
    echo "[$service]"
    echo -n "enabled: "
    systemctl is-enabled "$service" 2>/dev/null || true
    echo -n "active:  "
    systemctl is-active "$service" 2>/dev/null || true
done
echo

echo "--- Raspberry Pi Connect ---"
if command -v rpi-connect >/dev/null 2>&1; then
    echo
    echo "[rpi-connect status]"
    rpi-connect status || true

    echo
    echo "[rpi-connect doctor]"
    rpi-connect doctor || true
else
    echo "rpi-connect command not found"
fi
echo

echo "=== PCS Quick Summary ==="
echo

WIFI_STATE="unknown"
ETH_STATE="unknown"
DEFAULT_ROUTE="unknown"
ETH_ADDR_FOUND="no"
MODEM_STATUS="unknown"
GPSD_STATUS="unknown"
SAMBA_STATUS="unknown"
COCKPIT_STATUS="unknown"
CHRONY_STATUS="unknown"
CONTROL_PANEL_STATUS="unknown"
RTC_STATUS="unknown"
PRIMARY_SHARE_STATUS="unknown"
BACKUP_SHARE_STATUS="unknown"
BACKUP_SYNC_STATUS="unknown"

if command -v nmcli >/dev/null 2>&1; then
    WIFI_STATE="$(nmcli -t -f DEVICE,STATE device status | awk -F: -v dev="${PCS_WIFI_IFACE}" '$1 == dev {print $2}')"
    ETH_STATE="$(nmcli -t -f DEVICE,STATE,CONNECTION device status | awk -F: -v dev="${PCS_ETH_IFACE}" '$1 == dev {print $2 " (" $3 ")"}')"
fi

if ip route 2>/dev/null | grep -q "default .* ${PCS_WIFI_IFACE}"; then
    DEFAULT_ROUTE="${PCS_WIFI_IFACE}"
else
    DEFAULT_ROUTE="$(ip route 2>/dev/null | awk '/^default/ {print $5; exit}')"
fi

if ip -4 addr show dev "${PCS_ETH_IFACE}" 2>/dev/null | grep -q "${PCS_ROUTER_CIDR}"; then
    ETH_ADDR_FOUND="yes"
fi

if [[ -e /dev/rtc0 ]]; then
    RTC_STATUS="present"
else
    RTC_STATUS="missing"
fi

if systemctl is-active --quiet ModemManager; then
    if command -v mmcli >/dev/null 2>&1 && mmcli -L 2>&1 | grep -q "No modems were found"; then
        MODEM_STATUS="ModemManager active, no modem detected yet"
    else
        MODEM_STATUS="ModemManager active"
    fi
else
    MODEM_STATUS="ModemManager inactive"
fi

if systemctl is-active --quiet gpsd; then
    GPSD_STATUS="active"
else
    GPSD_STATUS="inactive / not configured yet"
fi

if systemctl is-active --quiet smbd; then
    SAMBA_STATUS="active"
else
    SAMBA_STATUS="inactive"
fi

if systemctl is-active --quiet cockpit.socket; then
    COCKPIT_STATUS="active"
else
    COCKPIT_STATUS="inactive"
fi

if systemctl is-active --quiet chrony; then
    CHRONY_STATUS="active"
else
    CHRONY_STATUS="inactive"
fi

if systemctl is-active --quiet "${PCS_CONTROL_SERVICE}"; then
    CONTROL_PANEL_STATUS="active"
else
    CONTROL_PANEL_STATUS="inactive"
fi

if [[ -d "${PCS_SHARE_PATH}" ]] && testparm -s 2>/dev/null | grep -q "^\[${PCS_SHARE_NAME}\]"; then
    PRIMARY_SHARE_STATUS="present"
else
    PRIMARY_SHARE_STATUS="missing"
fi

if [[ -d "${PCS_BACKUP_SHARE_PATH}" ]] && testparm -s 2>/dev/null | grep -q "^\[${PCS_BACKUP_SHARE_NAME}\]"; then
    BACKUP_SHARE_STATUS="present"
else
    BACKUP_SHARE_STATUS="missing"
fi

if [[ -f "${PCS_BACKUP_SHARE_PATH}/LAST_SYNC.txt" ]]; then
    BACKUP_SYNC_STATUS="$(cat "${PCS_BACKUP_SHARE_PATH}/LAST_SYNC.txt" 2>/dev/null || echo unknown)"
else
    BACKUP_SYNC_STATUS="no sync timestamp found"
fi

echo "Hostname:                 ${PCS_HOSTNAME}"
echo "Wi-Fi uplink ${PCS_WIFI_IFACE}:       ${WIFI_STATE}"
echo "Ethernet handoff ${PCS_ETH_IFACE}:    ${ETH_STATE}"
echo "Default route interface:  ${DEFAULT_ROUTE}"
echo "Router-side IP present:   ${ETH_ADDR_FOUND} (${PCS_ROUTER_CIDR})"
echo "RTC:                      ${RTC_STATUS}"
echo "Chrony/NTP:               ${CHRONY_STATUS}"
echo "Samba:                    ${SAMBA_STATUS}"
echo "Primary share:            ${PRIMARY_SHARE_STATUS} (${PCS_SHARE_NAME})"
echo "Backup share:             ${BACKUP_SHARE_STATUS} (${PCS_BACKUP_SHARE_NAME})"
echo "Last backup sync:         ${BACKUP_SYNC_STATUS}"
echo "Cockpit:                  ${COCKPIT_STATUS}"
echo "PCS Control Panel:        ${CONTROL_PANEL_STATUS} (${PCS_CONTROL_URL})"
echo "WWAN modem:               ${MODEM_STATUS}"
echo "GPSD:                     ${GPSD_STATUS}"
echo

echo "--- PCS Client Access Info ---"
echo
echo "Use these from a client behind the PCS/test router:"
echo
echo "Primary file share:"
echo "  \\\\${PCS_ROUTER_IP}\\${PCS_SHARE_NAME}"
echo
echo "Backup file share:"
echo "  \\\\${PCS_ROUTER_IP}\\${PCS_BACKUP_SHARE_NAME}"
echo
echo "Cockpit web UI:"
echo "  https://${PCS_ROUTER_IP}:9090"
echo

echo "PCS Control Panel:"
echo "  http://${PCS_ROUTER_IP}:${PCS_CONTROL_PORT}"
echo
echo "LAN NTP server:"
echo "  ${PCS_ROUTER_IP}"
echo
echo "Primary share path on Pi:"
echo "  ${PCS_SHARE_PATH}"
echo
echo "Backup share path on Pi:"
echo "  ${PCS_BACKUP_SHARE_PATH}"
echo

echo "--- Windows Client Test Commands ---"
echo
echo "Internet:"
echo "  ping 8.8.8.8"
echo "  ping google.com"
echo
echo "Pi/router-side access:"
echo "  ping ${PCS_ROUTER_IP}"
echo
echo "NTP:"
echo "  w32tm /stripchart /computer:${PCS_ROUTER_IP} /samples:5 /dataonly"
echo
echo "Primary file share:"
echo "  explorer \\\\${PCS_ROUTER_IP}\\${PCS_SHARE_NAME}"
echo
echo "Backup file share:"
echo "  explorer \\\\${PCS_ROUTER_IP}\\${PCS_BACKUP_SHARE_NAME}"
echo

echo "PCS Control Panel:"
echo "  start http://${PCS_ROUTER_IP}:${PCS_CONTROL_PORT}"
echo

echo "--- Current Test Topology ---"
echo
echo "Expected current test path:"
echo "  Client → test router Wi-Fi/LAN → router WAN → Pi ${PCS_ETH_IFACE} → Pi ${PCS_WIFI_IFACE} → home router/internet"
echo
echo "Future PCS path:"
echo "  Client → PCS router Wi-Fi/LAN → router WAN → Pi ${PCS_ETH_IFACE} → cellular modem → internet"
echo

echo "--- Notes ---"
echo
echo "- ${PCS_ROUTER_IP} is the stable router-side Pi address for PCS clients."
echo "- ${PCS_HOSTNAME}.local may not resolve from behind the router because mDNS usually does not cross router WAN/LAN boundaries."
echo "- ${PCS_SHARE_NAME} is the current primary/test share."
echo "- ${PCS_BACKUP_SHARE_NAME} is the SD-card backup mirror share."
echo "- Run ./scripts/sync-pcs-share-to-backup.sh to manually mirror the primary share to backup."
echo "- PCS Control Panel is available at http://${PCS_ROUTER_IP}:${PCS_CONTROL_PORT} on the router-side network."
echo "- No WWAN modem is expected until the EM7565 USB adapter is installed."
echo "- GPSD is expected to remain inactive until GPS/GNSS setup is configured."
echo

echo "=== End PCS Status ==="
