#!/usr/bin/env bash

set -Eeuo pipefail

SHARE_NAME="PCS-Share"
SHARE_PATH="/srv/pcs-share"
SAMBA_CONFIG="/etc/samba/smb.conf"
PCS_USER="${SUDO_USER:-$USER}"

echo
echo "=== PCS Samba Test Share Setup ==="
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
echo "Share name: ${SHARE_NAME}"
echo "Share path: ${SHARE_PATH}"
echo

echo "Creating share directory..."
${SUDO} mkdir -p "${SHARE_PATH}"
${SUDO} chown -R "${PCS_USER}:${PCS_USER}" "${SHARE_PATH}"
${SUDO} chmod 2775 "${SHARE_PATH}"

echo "Creating test file..."
echo "PCS Samba test file created on $(date)" | ${SUDO} tee "${SHARE_PATH}/README.txt" >/dev/null
${SUDO} chown "${PCS_USER}:${PCS_USER}" "${SHARE_PATH}/README.txt"

if [[ ! -f "${SAMBA_CONFIG}.stock" ]]; then
    echo "Backing up original Samba config to ${SAMBA_CONFIG}.stock..."
    ${SUDO} cp "${SAMBA_CONFIG}" "${SAMBA_CONFIG}.stock"
else
    echo "Original Samba config backup already exists."
fi

echo "Removing old PCS share block if present..."
${SUDO} sed -i '/# BEGIN PCS SAMBA SHARE/,/# END PCS SAMBA SHARE/d' "${SAMBA_CONFIG}"

echo "Adding PCS share block..."
cat <<EOF | ${SUDO} tee -a "${SAMBA_CONFIG}" >/dev/null

# BEGIN PCS SAMBA SHARE
[${SHARE_NAME}]
   path = ${SHARE_PATH}
   browseable = yes
   read only = no
   guest ok = no
   valid users = ${PCS_USER}
   force user = ${PCS_USER}
   create mask = 0664
   directory mask = 2775
# END PCS SAMBA SHARE
EOF

echo
echo "Setting Samba password for ${PCS_USER}."
echo "Use a simple temporary password for testing if desired."
${SUDO} smbpasswd -a "${PCS_USER}"

echo
echo "Testing Samba config..."
${SUDO} testparm -s

echo
echo "Restarting Samba..."
${SUDO} systemctl restart smbd
${SUDO} systemctl enable smbd >/dev/null 2>&1 || true

echo
echo "Local Samba share list:"
smbclient -L localhost -U "${PCS_USER}" || true

echo
echo "PCS Samba test share setup complete."
echo
echo "From Windows, try:"
echo "  \\\\pcs-pi.local\\${SHARE_NAME}"
echo
echo "If .local does not work, use the Pi IP address:"
echo "  hostname -I"
echo "Then:"
echo "  \\\\<pi-ip-address>\\${SHARE_NAME}"
echo
echo "Windows username:"
echo "  ${PCS_USER}"
echo "or:"
echo "  pcs-pi\\${PCS_USER}"
echo
