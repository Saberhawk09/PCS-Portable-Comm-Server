#!/usr/bin/env bash

set -Eeuo pipefail

PRIMARY_SHARE="/mnt/pcs-usb/PCS-Share"
BACKUP_SHARE="/srv/pcs-share-backup"
PCS_USER="${SUDO_USER:-$USER}"

echo
echo "=== PCS Share Backup Sync ==="
echo

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
    ${SUDO} -v
fi

if ! command -v rsync >/dev/null 2>&1; then
    echo "ERROR: rsync is not installed."
    echo "Install it with:"
    echo "  sudo apt install -y rsync"
    exit 1
fi

if ! findmnt /mnt/pcs-usb >/dev/null 2>&1; then
    echo "ERROR: USB primary storage is not mounted at /mnt/pcs-usb"
    echo "Run:"
    echo "  ./scripts/setup-usb-primary-share.sh"
    exit 1
fi

if [[ ! -d "${PRIMARY_SHARE}" ]]; then
    echo "ERROR: Primary USB share missing: ${PRIMARY_SHARE}"
    exit 1
fi

if [[ ! -d "${BACKUP_SHARE}" ]]; then
    echo "ERROR: Backup share missing: ${BACKUP_SHARE}"
    echo "Run ./scripts/setup-samba-backup-share.sh first."
    exit 1
fi

echo "Primary share: ${PRIMARY_SHARE}"
echo "Backup share:  ${BACKUP_SHARE}"
echo

echo "Sync mode:"
echo "  USB Primary → SD Backup"
echo
echo "WARNING:"
echo "  Files deleted from the USB primary share will also be deleted from the SD backup mirror."
echo

read -r -p "Continue with sync? [y/N] " answer

case "${answer}" in
    y|Y|yes|YES)
        ;;
    *)
        echo "Aborted."
        exit 0
        ;;
esac

echo
echo "Running rsync..."
${SUDO} rsync -rtvh --delete \
    --modify-window=2 \
    --exclude "LAST_SYNC.txt" \
    "${PRIMARY_SHARE}/" \
    "${BACKUP_SHARE}/"

echo
echo "Fixing ownership and permissions on SD backup..."
${SUDO} chown -R "${PCS_USER}:${PCS_USER}" "${BACKUP_SHARE}"
${SUDO} chmod -R u+rwX,g+rwX,o-rwx "${BACKUP_SHARE}"
${SUDO} find "${BACKUP_SHARE}" -type d -exec chmod 2775 {} \;

echo
echo "Writing sync timestamp..."
date | ${SUDO} tee "${BACKUP_SHARE}/LAST_SYNC.txt" >/dev/null
${SUDO} chown "${PCS_USER}:${PCS_USER}" "${BACKUP_SHARE}/LAST_SYNC.txt"

echo
echo "Backup sync complete."
echo
echo "Primary share:"
echo "  \\\\10.42.0.1\\PCS-Share"
echo
echo "Backup share:"
echo "  \\\\10.42.0.1\\PCS-Backup"
echo
