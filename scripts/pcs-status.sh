#!/usr/bin/env bash

echo "=== PCS System Status ==="
echo

echo "--- Hostname ---"
hostname
echo

echo "--- OS ---"
grep -E 'PRETTY_NAME|VERSION=' /etc/os-release
echo

echo "--- Kernel ---"
uname -a
echo

echo "--- Uptime ---"
uptime
echo

echo "--- Time / NTP / RTC ---"
timedatectl
echo

echo "--- RTC Devices ---"
ls -l /dev/rtc* 2>/dev/null || echo "No RTC devices found"
echo

echo "--- NetworkManager Devices ---"
nmcli device status 2>/dev/null || echo "nmcli not available"
echo

echo "--- IP Addresses ---"
ip -brief addr
echo

echo "--- Routes ---"
ip route
echo

echo "--- DNS ---"
resolvectl status 2>/dev/null || cat /etc/resolv.conf
echo

echo "--- USB Devices ---"
lsusb
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
    mmcli -L
else
    echo "mmcli not installed"
fi
echo

echo "--- Key Services ---"
for service in NetworkManager ModemManager avahi-daemon smbd gpsd chrony cockpit.socket rpi-connect; do
    echo
    echo "[$service]"
    systemctl is-enabled "$service" 2>/dev/null || true
    systemctl is-active "$service" 2>/dev/null || true
done

echo
echo "=== End PCS Status ==="
