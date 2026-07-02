#!/usr/bin/env bash

set -Eeuo pipefail

PRIMARY_SHARE="/srv/pcs-share"
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

if [[ ! -d "${PRIMARY_SHARE}" ]]; then
    echo "ERROR: Primary share missing: ${PRIMARY_SHARE}"
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
echo "  Primary → Backup"
echo
echo "WARNING:"
echo "  Files deleted from the primary share will also be deleted from the backup mirror."
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
${SUDO} rsync -avh --delete \
    --exclude "README.txt" \
    "${PRIMARY_SHARE}/" \
    "${BACKUP_SHARE}/"

echo
echo "Fixing ownership and permissions..."
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
echo "Backup share:"
echo "  \\\\10.42.0.1\\PCS-Backup"
echo
