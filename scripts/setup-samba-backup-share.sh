#!/usr/bin/env bash

set -Eeuo pipefail

SHARE_NAME="PCS-Backup"
SHARE_PATH="/srv/pcs-share-backup"
SAMBA_CONFIG="/etc/samba/smb.conf"
PCS_USER="${PCS_SAMBA_USER:-${SUDO_USER:-$USER}}"

echo
echo "=== PCS Samba Backup Share Setup ==="
echo

if ! command -v smbd >/dev/null 2>&1; then
    echo "ERROR: Samba does not appear to be installed."
    echo "Run ./scripts/install-dependencies.sh first."
    exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
    ${SUDO} -v
fi

echo "Using local user: ${PCS_USER}"
echo "Backup share name: ${SHARE_NAME}"
echo "Backup share path: ${SHARE_PATH}"
echo

echo "Creating backup share directory..."
${SUDO} mkdir -p "${SHARE_PATH}"
${SUDO} chown -R "${PCS_USER}:${PCS_USER}" "${SHARE_PATH}"
${SUDO} chmod 2775 "${SHARE_PATH}"

echo "Creating backup share README..."
cat <<EOF | ${SUDO} tee "${SHARE_PATH}/README.txt" >/dev/null
PCS backup share.

This share is intended to hold a mirror copy of the primary PCS file share.

Primary share:
  /srv/pcs-share

Backup share:
  /srv/pcs-share-backup

Use:
  ./scripts/sync-pcs-share-to-backup.sh

to manually sync the primary share to this backup location.
EOF

${SUDO} chown "${PCS_USER}:${PCS_USER}" "${SHARE_PATH}/README.txt"

if [[ ! -f "${SAMBA_CONFIG}.pre-pcs-backup-share" ]]; then
    echo "Backing up Samba config to ${SAMBA_CONFIG}.pre-pcs-backup-share..."
    ${SUDO} cp "${SAMBA_CONFIG}" "${SAMBA_CONFIG}.pre-pcs-backup-share"
else
    echo "Samba backup config already exists."
fi

echo "Removing old PCS backup share block if present..."
${SUDO} sed -i '/# BEGIN PCS SAMBA BACKUP SHARE/,/# END PCS SAMBA BACKUP SHARE/d' "${SAMBA_CONFIG}"

echo "Adding PCS backup share block..."
cat <<EOF | ${SUDO} tee -a "${SAMBA_CONFIG}" >/dev/null

# BEGIN PCS SAMBA BACKUP SHARE
[${SHARE_NAME}]
   path = ${SHARE_PATH}
   browseable = yes
   read only = no
   guest ok = no
   valid users = ${PCS_USER}
   force user = ${PCS_USER}
   create mask = 0664
   directory mask = 2775
# END PCS SAMBA BACKUP SHARE
EOF

echo
echo "Testing Samba config..."
${SUDO} testparm -s

echo
echo "Restarting Samba..."
${SUDO} systemctl restart smbd
${SUDO} systemctl enable smbd >/dev/null 2>&1 || true

echo
echo "PCS Samba backup share setup complete."
echo
echo "From Windows, try:"
echo "  \\\\10.42.0.1\\${SHARE_NAME}"
echo
echo "Username:"
echo "  ${PCS_USER}"
echo "or:"
echo "  pcs-pi\\${PCS_USER}"
echo
