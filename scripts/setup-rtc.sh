#!/usr/bin/env bash

set -Eeuo pipefail

CONFIG_FILE="/boot/firmware/config.txt"
BACKUP_FILE="/boot/firmware/config.txt.pre-pcs-rtc"
RTC_OVERLAY="dtoverlay=i2c-rtc,ds1307"
I2C_PARAM="dtparam=i2c_arm=on"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RTC_SEED_SRC="${REPO_DIR}/scripts/pcs-rtc-seed.sh"
RTC_SEED_DST="/usr/local/sbin/pcs-rtc-seed"
RTC_SEED_UNIT_SRC="${REPO_DIR}/systemd/pcs-rtc-seed.service"
RTC_SEED_UNIT_DST="/etc/systemd/system/pcs-rtc-seed.service"

echo
echo "=== PCS RTC Setup ==="
echo

if [[ ! -f "${CONFIG_FILE}" ]]; then
    echo "ERROR: ${CONFIG_FILE} not found."
    echo "This script expects Raspberry Pi OS Bookworm/Trixie-style boot config layout."
    exit 1
fi

if [[ ! -f "${RTC_SEED_SRC}" || ! -f "${RTC_SEED_UNIT_SRC}" ]]; then
    echo "ERROR: PCS RTC seed helper or systemd unit is missing from the repository."
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
echo "Installing guarded RTC boot-time seed service..."
${SUDO} install -o root -g root -m 0755 "${RTC_SEED_SRC}" "${RTC_SEED_DST}"
${SUDO} install -o root -g root -m 0644 "${RTC_SEED_UNIT_SRC}" "${RTC_SEED_UNIT_DST}"
${SUDO} systemctl daemon-reload
${SUDO} systemctl enable pcs-rtc-seed.service >/dev/null

if [[ -e /dev/rtc0 ]]; then
    echo
    echo "Checking the hardware RTC..."
    ${SUDO} "${RTC_SEED_DST}" --check

    if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qx "yes"; then
        echo "Writing the currently synchronized system time to the RTC in UTC."
        ${SUDO} hwclock --rtc=/dev/rtc0 --systohc --utc
    else
        echo "System time is not currently synchronized; leaving the RTC unchanged."
    fi

    echo "Starting the RTC seed service for this boot."
    ${SUDO} systemctl restart pcs-rtc-seed.service
else
    echo
    echo "The RTC device is not present yet. The enabled seed service will check it after reboot."
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
echo "  systemctl status pcs-rtc-seed.service --no-pager"
echo "  sudo /usr/local/sbin/pcs-rtc-seed --check"
echo
