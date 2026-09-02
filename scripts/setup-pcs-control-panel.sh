#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="/home/pi/Projects/PCS-Portable-Comm-Server"
CONTROL_PANEL_SRC="${REPO_DIR}/web/pcs-control-panel/pcs_control_panel.py"
REDIRECT_SRC="${REPO_DIR}/web/pcs-control-panel/pcs_dashboard_redirect.py"
DISPATCHER_SRC="${REPO_DIR}/scripts/pcs-web-action.sh"
DISPATCHER_DST="/usr/local/sbin/pcs-web-action"
PASSWORD_HELPER_SRC="${REPO_DIR}/scripts/pcs-admin-password-helper.py"
PASSWORD_HELPER_DST="/usr/local/sbin/pcs-admin-password-helper"
BACKUP_CONFIG_HELPER_SRC="${REPO_DIR}/scripts/pcs_backup_config.py"
BACKUP_CONFIG_HELPER_DST="/usr/local/sbin/pcs-backup-config"
APRS_TELEMETRY_SRC="${REPO_DIR}/scripts/pcs_aprs_telemetry.py"
APRS_TELEMETRY_DST="/usr/local/sbin/pcs-aprs-telemetry"
SERVICE_SRC="${REPO_DIR}/systemd/pcs-control-panel.service"
SERVICE_DST="/etc/systemd/system/pcs-control-panel.service"
REDIRECT_SERVICE_SRC="${REPO_DIR}/systemd/pcs-dashboard-redirect.service"
REDIRECT_SERVICE_DST="/etc/systemd/system/pcs-dashboard-redirect.service"
AUTH_DIR="/etc/pcs-control-panel"
ADMIN_FILE="${AUTH_DIR}/admin.json"
SESSION_KEY_FILE="${AUTH_DIR}/session.key"
BACKUP_ADMIN_USER="pcs-admin"
REMOVED_STANDBY_DIR="/opt/pcs-control-panel-standby"
REMOVED_STANDBY_SERVICE="/etc/systemd/system/pcs-control-panel-standby.service"
SUDOERS_FILE="/etc/sudoers.d/pcs-control-panel"
SUDOERS_TEMP=""
RESET_ADMIN_PASSWORD=0

cleanup() {
    if [[ -n "${SUDOERS_TEMP}" && -f "${SUDOERS_TEMP}" ]]; then
        rm -f -- "${SUDOERS_TEMP}"
    fi
}
trap cleanup EXIT

if [[ "${1:-}" == "--reset-admin-password" ]]; then
    RESET_ADMIN_PASSWORD=1
elif [[ -n "${1:-}" ]]; then
    echo "Usage: $0 [--reset-admin-password]"
    exit 2
fi

echo
echo "=== PCS Homepage and Control Panel Setup ==="
echo

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Do not run this script with sudo."
    echo "Run it as the normal pi user. It will ask for sudo when needed."
    exit 1
fi

if [[ "${RESET_ADMIN_PASSWORD}" -eq 1 && ! -t 0 ]]; then
    echo "ERROR: --reset-admin-password requires an interactive Pi terminal."
    echo "Rerun the installer interactively so the new password is not exposed in logs or arguments."
    exit 1
fi

if ! sudo -n true 2>/dev/null; then
    if [[ ! -t 0 ]]; then
        echo "ERROR: sudo credentials are required, but no interactive terminal is available."
        echo "Run this installer from an interactive Pi terminal so sudo can prompt normally."
        exit 1
    fi
    sudo -v
fi

for required_file in \
    "${CONTROL_PANEL_SRC}" \
    "${REDIRECT_SRC}" \
    "${DISPATCHER_SRC}" \
    "${PASSWORD_HELPER_SRC}" \
    "${BACKUP_CONFIG_HELPER_SRC}" \
    "${SERVICE_SRC}" \
    "${REDIRECT_SERVICE_SRC}"; do
    if [[ ! -f "${required_file}" ]]; then
        echo "ERROR: missing required file: ${required_file}"
        exit 1
    fi
done

echo "Removing obsolete legacy standby control panel, if present..."
sudo systemctl disable --now pcs-control-panel-standby.service >/dev/null 2>&1 || true
sudo rm -f -- "${REMOVED_STANDBY_SERVICE}"
sudo rm -rf -- "${REMOVED_STANDBY_DIR}"

echo "Installing root-owned PCS web action dispatcher..."
sudo install -o root -g root -m 0755 "${DISPATCHER_SRC}" "${DISPATCHER_DST}"

echo "Installing root-owned PCS admin password helper..."
sudo install -o root -g root -m 0755 "${PASSWORD_HELPER_SRC}" "${PASSWORD_HELPER_DST}"
sudo install -o root -g root -m 0755 "${BACKUP_CONFIG_HELPER_SRC}" "${BACKUP_CONFIG_HELPER_DST}"
sudo install -o root -g root -m 0755 "${APRS_TELEMETRY_SRC}" "${APRS_TELEMETRY_DST}"

echo "Installing sudoers allowlist..."
SUDOERS_TEMP="$(mktemp)"
cat >"${SUDOERS_TEMP}" <<EOF
# Allow the PCS web application to invoke only its fixed dispatcher actions.
pi ALL=(root) NOPASSWD: ${DISPATCHER_DST} dashboard-public-json, ${DISPATCHER_DST} dashboard-json, ${DISPATCHER_DST} status, ${DISPATCHER_DST} self-test, ${DISPATCHER_DST} meshtastic-status, ${DISPATCHER_DST} restart-meshtastic, ${DISPATCHER_DST} aprs-mailbox-read, ${DISPATCHER_DST} storage-status, ${DISPATCHER_DST} wifi-status, ${DISPATCHER_DST} wifi-connect, ${DISPATCHER_DST} wifi-disconnect, ${DISPATCHER_DST} cellular-status, ${DISPATCHER_DST} cellular-connect, ${DISPATCHER_DST} cellular-disconnect, ${DISPATCHER_DST} cellular-test, ${DISPATCHER_DST} sync-backup, ${DISPATCHER_DST} mount-usb, ${DISPATCHER_DST} mount-new-usb, ${DISPATCHER_DST} safe-unmount-usb, ${DISPATCHER_DST} restart-services, ${DISPATCHER_DST} restart-samba, ${DISPATCHER_DST} restart-modemmanager, ${DISPATCHER_DST} sync-time, ${DISPATCHER_DST} restart-chrony, ${DISPATCHER_DST} restart-gpsd, ${DISPATCHER_DST} restart-logs, ${DISPATCHER_DST} reboot-system, ${DISPATCHER_DST} shutdown-system
pi ALL=(root) NOPASSWD: ${PASSWORD_HELPER_DST} --change-from-stdin
pi ALL=(root) NOPASSWD: ${BACKUP_CONFIG_HELPER_DST} show, ${BACKUP_CONFIG_HELPER_DST} set-from-stdin
EOF
chmod 0600 "${SUDOERS_TEMP}"
sudo visudo -cf "${SUDOERS_TEMP}"
sudo install -o root -g root -m 0440 "${SUDOERS_TEMP}" "${SUDOERS_FILE}"
rm -f -- "${SUDOERS_TEMP}"
SUDOERS_TEMP=""

echo "Preparing local authentication files..."
if ! getent passwd "${BACKUP_ADMIN_USER}" >/dev/null; then
    sudo useradd --system --no-create-home --shell /usr/sbin/nologin "${BACKUP_ADMIN_USER}"
fi
sudo install -d -o root -g pi -m 0750 "${AUTH_DIR}"
if ! sudo test -s "${SESSION_KEY_FILE}"; then
    SESSION_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
    printf '%s\n' "${SESSION_KEY}" | sudo tee "${SESSION_KEY_FILE}" >/dev/null
    unset SESSION_KEY
fi
sudo chown root:pi "${SESSION_KEY_FILE}"
sudo chmod 0640 "${SESSION_KEY_FILE}"

CONFIGURE_ADMIN_PASSWORD=0
if [[ "${RESET_ADMIN_PASSWORD}" -eq 1 || ! -s "${ADMIN_FILE}" ]]; then
    CONFIGURE_ADMIN_PASSWORD=1
elif [[ -t 0 ]]; then
    echo
    read -r -p "A PCS admin password is already configured. Change it now? [y/N] " CHANGE_ADMIN_REPLY
    case "${CHANGE_ADMIN_REPLY}" in
        y|Y|yes|YES|Yes) CONFIGURE_ADMIN_PASSWORD=1 ;;
        *) echo "Preserving the existing PCS administrator credential." ;;
    esac
else
    echo "Preserving the existing PCS administrator credential."
fi

if [[ "${CONFIGURE_ADMIN_PASSWORD}" -eq 1 ]]; then
    if [[ -t 0 ]]; then
        echo
        echo "WARNING: A forgotten admin password cannot be recovered from the web interface."
        echo "Rerun this installer with --reset-admin-password to replace a forgotten password."
        sudo "${PASSWORD_HELPER_DST}" --set-password
    else
        echo "WARNING: no PCS admin password is configured and no interactive terminal is available."
        echo "The public homepage will run, but /admin/ will remain locked."
        echo "Configure it later with:"
        echo "  ./scripts/setup-pcs-control-panel.sh --reset-admin-password"
    fi
fi

if sudo test -s "${ADMIN_FILE}" \
    && ! sudo pdbedit -L 2>/dev/null | cut -d: -f1 | grep -Fxq "${BACKUP_ADMIN_USER}"; then
    if [[ ! -t 0 ]]; then
        echo "ERROR: PCS-Backup has not yet been synchronized with the PCS admin password." >&2
        echo "Rerun this installer interactively to initialize its dedicated Samba credential." >&2
        exit 1
    fi
    echo
    echo "Enter the current PCS admin password once to initialize PCS-Backup access."
    sudo "${PASSWORD_HELPER_DST}" --sync-samba-password
fi

if sudo test -s "${ADMIN_FILE}"; then
    sudo chown root:pi "${ADMIN_FILE}"
    sudo chmod 0640 "${ADMIN_FILE}"
fi

echo "Stopping the current web services for the port migration..."
sudo systemctl stop pcs-dashboard-redirect.service >/dev/null 2>&1 || true
sudo systemctl stop pcs-control-panel.service >/dev/null 2>&1 || true

echo "Installing systemd services..."
sudo install -o root -g root -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"
sudo install -o root -g root -m 0644 "${REDIRECT_SERVICE_SRC}" "${REDIRECT_SERVICE_DST}"
sudo systemctl daemon-reload

echo "Starting the unified port 80 homepage and admin service..."
sudo systemctl enable pcs-control-panel.service >/dev/null
sudo systemctl restart pcs-control-panel.service

echo "Starting the legacy port 8080 compatibility redirect..."
sudo systemctl enable pcs-dashboard-redirect.service >/dev/null
sudo systemctl restart pcs-dashboard-redirect.service

echo
echo "Testing local endpoints..."
curl -fsS --max-time 10 http://127.0.0.1/health
curl -fsS --max-time 20 http://127.0.0.1/ | grep -q "Admin Login"
curl -fsS --max-time 10 http://127.0.0.1:8080/health

echo
echo "Service status:"
systemctl status pcs-control-panel.service pcs-dashboard-redirect.service --no-pager -l || true

echo
echo "PCS web setup complete."
echo "  Public homepage:  http://10.42.0.1/"
echo "  Admin login:      http://10.42.0.1/admin/"
echo "  Legacy redirect:  http://10.42.0.1:8080/"
echo
echo "Home-network test address:"
echo "  http://192.168.50.236/"
echo
echo "If the admin password is forgotten, rerun:"
echo "  ./scripts/setup-pcs-control-panel.sh --reset-admin-password"
echo
echo "Keep these interfaces on the trusted PCS LAN."
