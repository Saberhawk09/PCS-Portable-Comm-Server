#!/usr/bin/env bash

set -Eeuo pipefail

SHARE_NAME="PCS-Backup"
SHARE_PATH="/srv/pcs-share-backup"
SAMBA_CONFIG="/etc/samba/smb.conf"
PCS_USER="${PCS_SAMBA_USER:-${SUDO_USER:-$USER}}"
BACKUP_ADMIN_USER="pcs-admin"
ADMIN_FILE="/etc/pcs-control-panel/admin.json"
PASSWORD_HELPER_SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/pcs-admin-password-helper.py"

echo
echo "=== PCS Samba Backup Share Setup ==="
echo

if [[ ! -x /usr/sbin/smbd ]]; then
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
echo "Backup login user: ${BACKUP_ADMIN_USER}"
echo

echo "Preparing the dedicated PCS-Backup login..."
if ! getent passwd "${BACKUP_ADMIN_USER}" >/dev/null; then
    ${SUDO} useradd --system --no-create-home --shell /usr/sbin/nologin "${BACKUP_ADMIN_USER}"
fi

# An upgrade may already have a web-admin credential but no dedicated Samba
# account. Synchronize it before changing the live share ACL so a rerun cannot
# accidentally lock PCS-Backup.
if ${SUDO} test -s "${ADMIN_FILE}" \
    && ! ${SUDO} pdbedit -L 2>/dev/null | cut -d: -f1 | grep -Fxq "${BACKUP_ADMIN_USER}"; then
    if [[ ! -t 0 ]]; then
        echo "ERROR: PCS-Backup needs a one-time administrator-password synchronization." >&2
        echo "Rerun this setup interactively; the password will not be logged or stored as plaintext." >&2
        exit 1
    fi
    echo
    echo "A PCS web-admin password already exists. Enter it once to initialize PCS-Backup access."
    ${SUDO} python3 "${PASSWORD_HELPER_SRC}" --sync-samba-password
fi

echo "Creating backup share directory..."
${SUDO} mkdir -p "${SHARE_PATH}"
${SUDO} chown -R "${PCS_USER}:${PCS_USER}" "${SHARE_PATH}"
${SUDO} chmod 2775 "${SHARE_PATH}"

echo "Creating backup share README..."
cat <<EOF | ${SUDO} tee "${SHARE_PATH}/README.txt" >/dev/null
PCS backup share.

This share is intended to hold a mirror copy of the primary PCS file share.

Primary share:
  /mnt/pcs-usb/PCS-Share

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
   valid users = ${BACKUP_ADMIN_USER}
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
echo "  ${BACKUP_ADMIN_USER}"
echo "or:"
echo "  PCS-FILE-SHARE\\${BACKUP_ADMIN_USER}"
echo
echo "Password:"
echo "  The current PCS web administrator password"
echo
