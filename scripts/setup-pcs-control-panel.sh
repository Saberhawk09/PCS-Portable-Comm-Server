#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="/home/pi/Projects/PCS-Portable-Comm-Server"
DISPATCHER_SRC="${REPO_DIR}/scripts/pcs-web-action.sh"
DISPATCHER_DST="/usr/local/sbin/pcs-web-action"
SERVICE_SRC="${REPO_DIR}/systemd/pcs-control-panel.service"
SERVICE_DST="/etc/systemd/system/pcs-control-panel.service"
SUDOERS_FILE="/etc/sudoers.d/pcs-control-panel"

echo
echo "=== PCS Control Panel Setup ==="
echo

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Do not run this script with sudo."
    echo "Run it as the normal pi user. It will ask for sudo when needed."
    exit 1
fi

sudo -v

if [[ ! -f "${DISPATCHER_SRC}" ]]; then
    echo "ERROR: missing dispatcher script: ${DISPATCHER_SRC}"
    exit 1
fi

if [[ ! -f "${SERVICE_SRC}" ]]; then
    echo "ERROR: missing service file: ${SERVICE_SRC}"
    exit 1
fi

echo "Installing root-owned PCS web action dispatcher..."
sudo install -o root -g root -m 0755 "${DISPATCHER_SRC}" "${DISPATCHER_DST}"

echo "Installing sudoers allowlist..."
cat <<EOF | sudo tee "${SUDOERS_FILE}" >/dev/null
# Allow the PCS web control panel to run only the PCS action dispatcher.
pi ALL=(root) NOPASSWD: ${DISPATCHER_DST} *
EOF

sudo chmod 0440 "${SUDOERS_FILE}"

echo "Validating sudoers file..."
sudo visudo -cf "${SUDOERS_FILE}"

echo "Installing systemd service..."
sudo cp "${SERVICE_SRC}" "${SERVICE_DST}"
sudo systemctl daemon-reload
sudo systemctl enable --now pcs-control-panel.service

echo
echo "PCS Control Panel service status:"
systemctl status pcs-control-panel.service --no-pager -l || true

echo
echo "PCS Control Panel setup complete."
echo
echo "From a client behind the PCS/test router, open:"
echo "  http://10.42.0.1:8080"
echo
echo "From the Pi/home network, it may also be reachable at:"
echo "  http://pcs-pi.local:8080"
echo "  http://192.168.50.236:8080"
echo
echo "Do not expose this interface to the public internet."
echo
