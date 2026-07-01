#!/usr/bin/env bash

set -u

echo "=== PCS System Status ==="
echo

echo "--- Hostname ---"
hostname
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

echo "--- NetworkManager Devices ---"
if command -v nmcli >/dev/null 2>&1; then
    nmcli device status
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

echo "--- Key Services ---"
for service in NetworkManager ModemManager avahi-daemon smbd gpsd chrony cockpit.socket; do
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

echo "=== End PCS Status ==="
