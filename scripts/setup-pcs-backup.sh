#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_HELPER_SRC="${SCRIPT_DIR}/pcs_backup_config.py"
CONFIG_HELPER_DST="/usr/local/sbin/pcs-backup-config"
AUTO_BACKUP_SRC="${SCRIPT_DIR}/pcs_auto_backup.py"
AUTO_BACKUP_DST="/usr/local/sbin/pcs-auto-backup"
DISPATCHER_SRC="${SCRIPT_DIR}/pcs-web-action.sh"
DISPATCHER_DST="/usr/local/sbin/pcs-web-action"
SERVICE_SRC="${REPO_DIR}/systemd/pcs-backup.service"
SERVICE_DST="/etc/systemd/system/pcs-backup.service"
TIMER_SRC="${REPO_DIR}/systemd/pcs-backup.timer"
TIMER_DST="/etc/systemd/system/pcs-backup.timer"

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: run this script as the normal Pi user, not with sudo." >&2
    exit 1
fi

if ! sudo -n true 2>/dev/null; then
    [[ -t 0 ]] || { echo "ERROR: interactive sudo credentials are required." >&2; exit 1; }
    sudo -v
fi

for required in "${CONFIG_HELPER_SRC}" "${AUTO_BACKUP_SRC}" "${DISPATCHER_SRC}" "${SERVICE_SRC}" "${TIMER_SRC}"; do
    [[ -f "${required}" ]] || { echo "ERROR: missing required file: ${required}" >&2; exit 1; }
done

echo "Installing PCS automatic backup components..."
sudo install -d -o root -g root -m 0755 /etc/pcs-backup
sudo install -o root -g root -m 0755 "${CONFIG_HELPER_SRC}" "${CONFIG_HELPER_DST}"
sudo install -o root -g root -m 0755 "${AUTO_BACKUP_SRC}" "${AUTO_BACKUP_DST}"
sudo install -o root -g root -m 0755 "${DISPATCHER_SRC}" "${DISPATCHER_DST}"
sudo install -o root -g root -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"
sudo install -o root -g root -m 0644 "${TIMER_SRC}" "${TIMER_DST}"
sudo systemctl daemon-reload
sudo "${CONFIG_HELPER_DST}" initialize

echo "Validating PCS automatic backup installation..."
sudo "${CONFIG_HELPER_DST}" show | python3 -m json.tool >/dev/null
sudo systemctl cat pcs-backup.service pcs-backup.timer >/dev/null
if [[ "$(sudo "${CONFIG_HELPER_DST}" show)" == *'"enabled":true'* ]]; then
    sudo systemctl is-enabled --quiet pcs-backup.timer
    sudo systemctl is-active --quiet pcs-backup.timer
fi

echo "PCS automatic backup is installed. Existing configuration was preserved."
