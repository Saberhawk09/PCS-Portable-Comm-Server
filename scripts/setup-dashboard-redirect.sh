#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="/home/pi/Projects/PCS-Portable-Comm-Server"
SCRIPT_PATH="${REPO_DIR}/web/pcs-control-panel/pcs_dashboard_redirect.py"
SERVICE_SRC="${REPO_DIR}/systemd/pcs-dashboard-redirect.service"
SERVICE_DST="/etc/systemd/system/pcs-dashboard-redirect.service"

echo
echo "=== PCS Legacy Admin Redirect Setup ==="
echo

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Do not run this script with sudo."
    echo "Run it as the normal pi user. It will ask for sudo when needed."
    exit 1
fi

if ! sudo -n true 2>/dev/null; then
    if [[ ! -t 0 ]]; then
        echo "ERROR: sudo credentials are required, but no interactive terminal is available."
        exit 1
    fi
    sudo -v
fi

if [[ ! -f "${SCRIPT_PATH}" || ! -f "${SERVICE_SRC}" ]]; then
    echo "ERROR: compatibility redirect source or service file is missing."
    exit 1
fi

if ! systemctl is-active --quiet pcs-control-panel.service; then
    echo "ERROR: pcs-control-panel.service must be active before installing the compatibility redirect."
    echo "Run ./scripts/setup-pcs-control-panel.sh first."
    exit 1
fi

echo "Installing the port 8080 compatibility redirect..."
sudo systemctl stop pcs-dashboard-redirect.service >/dev/null 2>&1 || true
sudo install -o root -g root -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"
sudo systemctl daemon-reload
sudo systemctl enable --now pcs-dashboard-redirect.service

echo
echo "Testing compatibility redirect health..."
curl -fsS --max-time 5 http://127.0.0.1:8080/health

echo
echo "Legacy admin bookmarks now redirect as follows:"
echo "  http://10.42.0.1:8080/ -> http://10.42.0.1/admin/"
