#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Do not run this script with sudo."
    echo "Run it as the normal Pi user. The script will ask for sudo when needed."
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOCKET_SRC="${REPO_DIR}/systemd/pcs-gpsd-lan.socket"
SERVICE_SRC="${REPO_DIR}/systemd/pcs-gpsd-lan.service"
SOCKET_DEST="/etc/systemd/system/pcs-gpsd-lan.socket"
SERVICE_DEST="/etc/systemd/system/pcs-gpsd-lan.service"
PROXY_BIN="/lib/systemd/systemd-socket-proxyd"
LAN_ADDRESS="10.42.0.1"
GPSD_PORT="2947"

echo
echo "=== PCS LAN-only GPSD Proxy Setup ==="
echo
echo "This keeps gpsd bound to localhost and publishes a proxy only on:"
echo "  ${LAN_ADDRESS}:${GPSD_PORT}"
echo
echo "Use this for trusted PCS LAN clients such as Pi-Star YSFGateway."
echo "It does not expose gpsd on the WWAN or Wi-Fi uplink interfaces."
echo

for required_file in "${SOCKET_SRC}" "${SERVICE_SRC}"; do
    if [[ ! -f "${required_file}" ]]; then
        echo "ERROR: Required file not found: ${required_file}"
        exit 1
    fi
done

if [[ ! -x "${PROXY_BIN}" ]]; then
    echo "ERROR: ${PROXY_BIN} was not found."
    echo "This setup requires systemd-socket-proxyd."
    exit 1
fi

if [[ "${PCS_ASSUME_YES:-}" == "1" || "${PCS_GPSD_LAN_CONFIRM:-}" == "yes" ]]; then
    answer="yes"
    echo "Continue with LAN-only GPSD proxy setup? [Y/N] yes"
else
    read -r -p "Continue with LAN-only GPSD proxy setup? [Y/N] " answer
fi

case "${answer}" in
    y|Y|yes|YES|Yes)
        ;;
    *)
        echo "Aborted."
        exit 0
        ;;
esac

if ss -lnt 2>/dev/null | grep -Eq '(^|[[:space:]])(\*|0\.0\.0\.0|\[::\]):2947([[:space:]]|$)'; then
    echo "ERROR: gpsd already appears to be exposed on every interface."
    echo "Remove -G from GPSD_OPTIONS before installing the LAN-only proxy."
    exit 1
fi

echo
echo "--- Installing systemd units ---"
sudo install -o root -g root -m 0644 "${SOCKET_SRC}" "${SOCKET_DEST}"
sudo install -o root -g root -m 0644 "${SERVICE_SRC}" "${SERVICE_DEST}"

echo
echo "--- Enabling LAN-only GPSD socket ---"
sudo systemctl daemon-reload
sudo systemctl enable --now pcs-gpsd-lan.socket

echo
echo "--- Verifying socket binding ---"
sudo systemctl status pcs-gpsd-lan.socket --no-pager -l || true

if ! ss -lnt 2>/dev/null | grep -Eq '10\.42\.0\.1:2947([[:space:]]|$)'; then
    echo "ERROR: ${LAN_ADDRESS}:${GPSD_PORT} is not listening."
    exit 1
fi

if ! systemctl is-active --quiet gpsd.service; then
    echo "WARNING: gpsd.service is not active."
    echo "The proxy socket is installed, but client requests will fail until gpsd is running."
else
    echo "gpsd.service is active."
fi

echo
echo "--- GPSD protocol check through the LAN address ---"
python3 - "${LAN_ADDRESS}" "${GPSD_PORT}" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

with socket.create_connection((host, port), timeout=3) as client:
    client.sendall(b"?VERSION;\n")
    reply = client.recv(2048).decode("utf-8", "replace").strip()

if '"class":"VERSION"' not in reply:
    raise SystemExit(f"ERROR: Unexpected GPSD reply: {reply[:200]}")

print("GPSD VERSION response received through the LAN-only proxy.")
PY

echo
echo "PCS LAN-only GPSD proxy setup complete."
echo
echo "Pi-Star YSFGateway settings:"
echo "  [GPSD]"
echo "  Enable=1"
echo "  Address=${LAN_ADDRESS}"
echo "  Port=${GPSD_PORT}"
