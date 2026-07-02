#!/usr/bin/env bash

set -Eeuo pipefail

CHRONY_CONFIG="/etc/chrony/chrony.conf"
BACKUP_FILE="/etc/chrony/chrony.conf.pre-pcs-lan-ntp"

PCS_NTP_BLOCK_START="# BEGIN PCS LAN NTP"
PCS_NTP_BLOCK_END="# END PCS LAN NTP"

PCS_NTP_NETWORK="10.42.0.0/24"

echo
echo "=== PCS Chrony LAN NTP Setup ==="
echo

if ! command -v chronyd >/dev/null 2>&1; then
    echo "ERROR: chrony does not appear to be installed."
    echo "Run ./scripts/install-dependencies.sh first."
    exit 1
fi

if ! command -v chronyc >/dev/null 2>&1; then
    echo "ERROR: chronyc not found."
    echo "Run ./scripts/install-dependencies.sh first."
    exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
    ${SUDO} -v
fi

if [[ ! -f "${CHRONY_CONFIG}" ]]; then
    echo "ERROR: ${CHRONY_CONFIG} not found."
    exit 1
fi

echo "Using Chrony config: ${CHRONY_CONFIG}"
echo

if [[ ! -f "${BACKUP_FILE}" ]]; then
    echo "Creating backup: ${BACKUP_FILE}"
    ${SUDO} cp "${CHRONY_CONFIG}" "${BACKUP_FILE}"
else
    echo "Backup already exists: ${BACKUP_FILE}"
fi

echo
echo "Removing old PCS LAN NTP block if present..."
${SUDO} sed -i "/${PCS_NTP_BLOCK_START}/,/${PCS_NTP_BLOCK_END}/d" "${CHRONY_CONFIG}"

echo
echo "Checking for existing active rtcsync directive..."

if grep -qE "^[[:space:]]*rtcsync[[:space:]]*$" "${CHRONY_CONFIG}"; then
    RTC_SYNC_LINE="# rtcsync already enabled elsewhere in chrony.conf"
    echo "rtcsync already enabled elsewhere."
else
    RTC_SYNC_LINE="rtcsync"
    echo "rtcsync will be added in the PCS block."
fi

echo
echo "Adding PCS LAN NTP block..."

cat <<EOF | ${SUDO} tee -a "${CHRONY_CONFIG}" >/dev/null

${PCS_NTP_BLOCK_START}
# Allow NTP clients on the PCS router-facing Ethernet network.
allow ${PCS_NTP_NETWORK}

# Keep the hardware RTC updated from the synchronized system clock when possible.
# This helps the RTC provide a useful boot-time fallback later.
${RTC_SYNC_LINE}

# Allow this Pi to serve time even if upstream internet NTP is unavailable.
# The RTC seeds the system clock at boot; Chrony then serves the system clock.
# Later, GPS/GNSS will become the preferred offline time source.
local stratum 10
${PCS_NTP_BLOCK_END}
EOF

echo
echo "Restarting Chrony..."
${SUDO} systemctl restart chrony
${SUDO} systemctl enable chrony >/dev/null 2>&1 || true

echo
echo "PCS Chrony config block:"
grep -n "${PCS_NTP_BLOCK_START}" -A12 "${CHRONY_CONFIG}" || true

echo
echo "Chrony tracking:"
chronyc tracking || true

echo
echo "Chrony sources:"
chronyc sources -v || true

echo
echo "PCS LAN NTP setup complete."
echo
echo "Clients on the router-side network should use:"
echo "  10.42.0.1"
echo
echo "From Windows, test with:"
echo "  w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly"
echo
