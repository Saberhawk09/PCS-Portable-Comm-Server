#!/usr/bin/env bash

set -Eeuo pipefail

CONFIG_FILE="/boot/firmware/config.txt"
BACKUP_FILE="/boot/firmware/config.txt.pre-pcs-rtc"
RTC_OVERLAY="dtoverlay=i2c-rtc,ds1307"
I2C_PARAM="dtparam=i2c_arm=on"

echo
echo "=== PCS RTC Setup ==="
echo

if [[ ! -f "${CONFIG_FILE}" ]]; then
    echo "ERROR: ${CONFIG_FILE} not found."
    echo "This script expects Raspberry Pi OS Bookworm/Trixie-style boot config layout."
    exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
    ${SUDO} -v
fi

echo "Using config file: ${CONFIG_FILE}"
echo

if [[ ! -f "${BACKUP_FILE}" ]]; then
    echo "Creating backup: ${BACKUP_FILE}"
    ${SUDO} cp "${CONFIG_FILE}" "${BACKUP_FILE}"
else
    echo "Backup already exists: ${BACKUP_FILE}"
fi

echo
echo "Checking I2C setting..."

if grep -qE "^[[:space:]]*${I2C_PARAM}[[:space:]]*$" "${CONFIG_FILE}"; then
    echo "I2C already enabled."
else
    echo "Adding I2C enable line."
    {
        echo
        echo "# PCS RTC support"
        echo "${I2C_PARAM}"
    } | ${SUDO} tee -a "${CONFIG_FILE}" >/dev/null
fi

echo
echo "Checking RTC overlay..."

if grep -qE "^[[:space:]]*${RTC_OVERLAY}[[:space:]]*$" "${CONFIG_FILE}"; then
    echo "RTC overlay already present."
else
    echo "Adding RTC overlay line."
    {
        echo "${RTC_OVERLAY}"
    } | ${SUDO} tee -a "${CONFIG_FILE}" >/dev/null
fi

echo
echo "PCS RTC config complete."
echo
echo "This script intentionally does not modify HDMI/display settings."
echo
echo "Reboot required:"
echo "  sudo reboot"
echo
echo "After reboot, verify with:"
echo "  ls /dev/rtc*"
echo "  dmesg | grep -i rtc"
echo "  timedatectl"
echo
