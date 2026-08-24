#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Do not run this script with sudo."
    echo "Run it as the normal Pi user. The script will ask for sudo when needed."
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

STARTER_SRC="${REPO_DIR}/scripts/pcs-wwan-gps-nmea-start.py"
SERVICE_SRC="${REPO_DIR}/systemd/pcs-wwan-gps-nmea.service"

GPSD_DEFAULT="/etc/default/gpsd"
CHRONY_DROPIN_DIR="/etc/chrony/conf.d"
CHRONY_DROPIN="${CHRONY_DROPIN_DIR}/pcs-wwan-gps.conf"

echo
echo "=== PCS WWAN modem NMEA GPS to gpsd to Chrony Setup ==="
echo
echo "This configures:"
echo "  - WWAN modem GPS NMEA on /dev/ttyUSB1"
echo "  - gpsd reading /dev/ttyUSB1"
echo "  - Chrony reading gpsd SHM refclock 0"
echo
echo "This does not change AT!USBCOMP."
echo

if [[ "${PCS_ASSUME_YES:-}" == "1" || "${PCS_WWAN_GPS_CONFIRM:-}" == "yes" ]]; then
    answer="yes"
    echo "Continue with WWAN modem NMEA GPS setup? [Y/N] yes"
else
    read -r -p "Continue with WWAN modem NMEA GPS setup? [Y/N] " answer
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
echo "--- Installing packages ---"
sudo apt update
sudo apt install -y gpsd gpsd-clients chrony

echo
echo "--- Installing WWAN modem GPS starter ---"
sudo install -o root -g root -m 0755 "${STARTER_SRC}" /usr/local/sbin/pcs-wwan-gps-nmea-start.py
sudo install -o root -g root -m 0644 "${SERVICE_SRC}" /etc/systemd/system/pcs-wwan-gps-nmea.service

echo
echo "--- Configuring gpsd ---"
sudo cp "${GPSD_DEFAULT}" "${GPSD_DEFAULT}.pcs-wwan.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true

sudo tee "${GPSD_DEFAULT}" >/dev/null <<'EOF'
# PCS WWAN modem GPS configuration
START_DAEMON="true"
GPSD_OPTIONS="-n"
DEVICES="/dev/ttyUSB1"
USBAUTO="false"
GPSD_SOCKET="/var/run/gpsd.sock"
EOF

echo
echo "--- Configuring Chrony gpsd SHM source ---"
sudo mkdir -p "${CHRONY_DROPIN_DIR}"

sudo tee "${CHRONY_DROPIN}" >/dev/null <<'EOF'
# PCS WWAN modem GPS via gpsd
# gpsd publishes NMEA time to SHM 0. This is GPS NMEA timing without PPS.
# Prefer GPS whenever it is selectable. Chrony still rejects invalid,
# unreachable, or inconsistent samples and then uses Internet NTP.
refclock SHM 0 refid GPS precision 1e-1 poll 4 delay 0.2 prefer
EOF

echo
echo "--- Reloading systemd ---"
sudo systemctl daemon-reload

echo
echo "--- Disable gpsd socket activation so gpsd runs as an explicit service ---"
sudo systemctl disable --now gpsd.socket 2>/dev/null || true
sudo systemctl stop gpsd.service 2>/dev/null || true

echo
echo "--- Enable services ---"
sudo systemctl enable ModemManager
sudo systemctl enable pcs-wwan-gps-nmea.service
sudo systemctl enable gpsd.service

MODEM_FOUND=0

wait_for_modemmanager_modem() {
    local attempts="$1"
    local label="$2"

    MODEM_FOUND=0

    for i in $(seq 1 "${attempts}"); do
        if mmcli -L 2>/dev/null | grep -q "/Modem/"; then
            MODEM_FOUND=1
            echo "ModemManager detected modem."
            return 0
        fi

        echo "Waiting for ModemManager modem detection (${label})... ${i}/${attempts}"
        sudo mmcli --scan-modems >/dev/null 2>&1 || true
        sleep 5
    done

    return 1
}

soft_reset_wwan_usb_device() {
    echo
    echo "--- Soft-reset WWAN USB device ---"

    echo "Stopping ModemManager before USB soft replug."
    sudo systemctl stop ModemManager 2>/dev/null || true

    if [[ ! -e /dev/cdc-wdm0 ]]; then
        echo "Cannot soft-reset modem: /dev/cdc-wdm0 is missing."
        return 1
    fi

    local iface_path
    local search_path
    local usb_device_path=""

    iface_path="$(readlink -f /sys/class/usbmisc/cdc-wdm0/device 2>/dev/null || true)"

    if [[ -z "${iface_path}" ]]; then
        echo "Cannot resolve sysfs path for /dev/cdc-wdm0."
        return 1
    fi

    search_path="${iface_path}"

    while [[ -n "${search_path}" && "${search_path}" != "/" ]]; do
        if [[ -e "${search_path}/authorized" && -e "${search_path}/idVendor" && -e "${search_path}/idProduct" ]]; then
            usb_device_path="${search_path}"
            break
        fi

        search_path="$(dirname "${search_path}")"
    done

    if [[ -z "${usb_device_path}" ]]; then
        echo "Cannot find USB device authorized control."
        echo "Resolved cdc-wdm0 sysfs path:"
        echo "  ${iface_path}"
        return 1
    fi

    echo "Soft-disconnecting modem USB device:"
    echo "  ${usb_device_path}"

    echo 0 | sudo tee "${usb_device_path}/authorized" >/dev/null
    sleep 5

    echo "Soft-reconnecting modem USB device."
    echo 1 | sudo tee "${usb_device_path}/authorized" >/dev/null

    echo "Waiting for modem device nodes to return..."
    for i in $(seq 1 24); do
        if [[ -e /dev/cdc-wdm0 && -e /dev/ttyUSB1 ]]; then
            break
        fi
        sleep 5
    done

    sudo udevadm settle --timeout=10 >/dev/null 2>&1 || true
}

echo
echo "--- Prepare WWAN USB before ModemManager detection ---"
if soft_reset_wwan_usb_device; then
    echo "WWAN USB soft replug complete."
else
    echo "WARNING: WWAN USB soft replug was skipped or failed."
    echo "Continuing with ModemManager detection in case the modem is already usable."
fi

echo
echo "--- Start ModemManager and wait for WWAN modem detection ---"
sudo systemctl restart ModemManager
sleep 10
sudo mmcli --scan-modems >/dev/null 2>&1 || true

start_modemmanager_after_usb_reset() {
    sudo systemctl restart ModemManager
    sleep 10
    sudo mmcli --scan-modems >/dev/null 2>&1 || true
}


if ! wait_for_modemmanager_modem 12 "initial"; then
    echo
    echo "ModemManager did not detect modem during initial wait."
    echo "Kernel USB devices may exist while ModemManager still fails to claim the modem."
    echo "Trying a software USB replug before failing..."

    if soft_reset_wwan_usb_device; then
        start_modemmanager_after_usb_reset
        wait_for_modemmanager_modem 24 "after USB soft reset" || true
    fi
fi

if [[ "${MODEM_FOUND}" -ne 1 ]]; then
    echo "ERROR: ModemManager did not detect the modem."
    echo "Check USB modem enumeration:"
    echo "  lsusb"
    echo "  ls -l /dev/ttyUSB* /dev/cdc-wdm*"
    echo "  journalctl -u ModemManager -n 120 --no-pager"
    echo
    echo "Manual recovery that previously worked:"
    echo "  unplug/replug the WWAN USB adapter, then rerun this script"
    exit 1
fi

echo "--- Start GPS starter, gpsd, and Chrony ---"
sudo systemctl restart chrony
sudo systemctl reset-failed pcs-wwan-gps-nmea.service 2>/dev/null || true
sudo systemctl restart pcs-wwan-gps-nmea.service
sudo systemctl restart gpsd.service

echo
echo "--- Waiting for gpsd and Chrony samples ---"
sleep 45

echo
echo "--- WWAN modem GPS starter status ---"
systemctl status pcs-wwan-gps-nmea.service --no-pager -l || true

echo
echo "--- gpsd status ---"
systemctl status gpsd.service --no-pager -l || true

echo
echo "--- Raw gpsd NMEA check, coordinates redacted ---"
cat > /tmp/pcs-redact-nmea.py <<'PY'
#!/usr/bin/env python3

import sys

count = 0

for raw in sys.stdin:
    line = raw.strip()

    if not line.startswith("$G"):
        continue

    parts = line.split(",")
    kind = parts[0]

    if kind.endswith("RMC") and len(parts) > 6:
        parts[3] = "[LAT]"
        parts[4] = "[N/S]"
        parts[5] = "[LON]"
        parts[6] = "[E/W]"
    elif kind.endswith("GGA") and len(parts) > 5:
        parts[2] = "[LAT]"
        parts[3] = "[N/S]"
        parts[4] = "[LON]"
        parts[5] = "[E/W]"
    elif kind.endswith("GLL") and len(parts) > 4:
        parts[1] = "[LAT]"
        parts[2] = "[N/S]"
        parts[3] = "[LON]"
        parts[4] = "[E/W]"

    print(",".join(parts))
    count += 1

    if count >= 8:
        break

if count == 0:
    print("No NMEA received from gpsd during quick check.")
PY

if command -v gpspipe >/dev/null 2>&1; then
    GPSPIPE_OUTPUT="$(mktemp)"

    if timeout 20 gpspipe -r > "${GPSPIPE_OUTPUT}" 2>/dev/null; then
        true
    else
        echo "gpspipe quick check ended after timeout or returned nonzero; continuing."
    fi

    python3 /tmp/pcs-redact-nmea.py < "${GPSPIPE_OUTPUT}"
    rm -f "${GPSPIPE_OUTPUT}"
else
    echo "gpspipe not available."
fi

rm -f /tmp/pcs-redact-nmea.py

echo
echo "--- Chrony sources ---"
chronyc sources -v || true

echo
echo "--- Chrony sourcestats ---"
chronyc sourcestats -v || true

echo
echo "--- Chrony tracking ---"
chronyc tracking || true

echo
echo "=== PCS WWAN modem NMEA GPS setup complete ==="
