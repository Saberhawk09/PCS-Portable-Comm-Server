#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="/home/pi/Projects/PCS-Portable-Comm-Server"
SCRIPT_PATH="${REPO_DIR}/web/pcs-control-panel/pcs_dashboard_redirect.py"
SERVICE_SRC="${REPO_DIR}/systemd/pcs-dashboard-redirect.service"
SERVICE_DST="/etc/systemd/system/pcs-dashboard-redirect.service"

echo
echo "=== PCS Dashboard Redirect Setup ==="
echo

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Do not run this script with sudo."
    echo "Run it as the normal pi user. It will ask for sudo when needed."
    exit 1
fi

sudo -v

if [[ ! -f "${SCRIPT_PATH}" ]]; then
    echo "ERROR: missing redirect script:"
    echo "  ${SCRIPT_PATH}"
    exit 1
fi

if [[ ! -f "${SERVICE_SRC}" ]]; then
    echo "ERROR: missing systemd service file:"
    echo "  ${SERVICE_SRC}"
    exit 1
fi

echo "Checking whether TCP port 80 is already in use..."
if sudo ss -ltnp | awk '{print $4}' | grep -Eq '(:|\]:)80$'; then
    echo "ERROR: TCP port 80 is already in use."
    echo
    sudo ss -ltnp | grep -E '(:|\]:)80[[:space:]]' || true
    echo
    echo "Stop the conflicting service before installing the PCS dashboard redirect."
    exit 1
fi

echo "Making redirect script executable..."
chmod +x "${SCRIPT_PATH}"

echo "Installing systemd service..."
sudo cp "${SERVICE_SRC}" "${SERVICE_DST}"

echo "Reloading systemd..."
sudo systemctl daemon-reload

echo "Enabling and starting pcs-dashboard-redirect.service..."
sudo systemctl enable --now pcs-dashboard-redirect.service

echo
echo "Service status:"
systemctl status pcs-dashboard-redirect.service --no-pager -l || true

echo
echo "Testing local redirect health endpoint..."
curl -fsS http://127.0.0.1/health || true

echo
echo "PCS dashboard redirect setup complete."
echo
echo "From a client behind the PCS/test router, open:"
echo "  http://10.42.0.1"
echo
echo "It should redirect to:"
echo "  http://10.42.0.1:8080"
echo
