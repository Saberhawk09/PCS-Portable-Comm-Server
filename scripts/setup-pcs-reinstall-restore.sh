#!/usr/bin/env bash
set -Eeuo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ ${EUID} -ne 0 && $# -eq 2 ]] || { echo "Run as pi: $0 --network|--api|--private TRUSTED_DECRYPTED_DIRECTORY" >&2; exit 2; }
mode="$1"
directory="$2"
case "${mode}" in
    --network) component=network ;;
    --api) component=api ;;
    --private) component=private ;;
    *) echo "Unknown recovery phase" >&2; exit 2 ;;
esac
sudo -n true
sudo python3 "${REPO_DIR}/scripts/pcs_reinstall_restore.py" "${component}" "${directory}" --check
replace_args=()
if [[ "${PCS_REINSTALL_EXACT:-no}" == "yes" && "${component}" != "private" ]]; then
    replace_args=(--replace)
fi
if [[ "${component}" == "network" ]]; then
    if systemctl is-active --quiet wg-quick@wg-pcs.service; then
        echo "ERROR: recovery must not replace an active VPN; use a fresh install." >&2
        exit 1
    fi
    sudo python3 "${REPO_DIR}/scripts/pcs_reinstall_restore.py" network "${directory}" "${replace_args[@]}"
    # Load saved profiles without switching the uplink used by the installer.
    sudo nmcli connection reload
    if sudo test -f "${directory}/etc/pcs/wireguard-management.conf"; then
        bash "${REPO_DIR}/scripts/setup-wireguard-management.sh" --validate-config
    fi
elif [[ "${component}" == "api" ]] && sudo test -d "${directory}/etc/pcs-stats-api"; then
    PCS_API_PREPARE_CONFIRM=yes bash "${REPO_DIR}/scripts/setup-pcs-stats-api.sh" --prepare
    sudo python3 "${REPO_DIR}/scripts/pcs_reinstall_restore.py" api "${directory}" "${replace_args[@]}"
    PCS_API_ACTIVATE_CONFIRM=yes bash "${REPO_DIR}/scripts/setup-pcs-stats-api.sh" --activate
elif [[ "${component}" == "private" ]]; then
    sudo systemctl stop smbd.service bluetooth.service pcs-meshtastic.service \
        pcs-control-panel.service direwolf.service graywolf.service >/dev/null 2>&1 || true
    sudo python3 "${REPO_DIR}/scripts/pcs_reinstall_restore.py" private "${directory}"
    sudo pdbedit -L >/dev/null
    sudo systemctl disable --now direwolf.service graywolf.service >/dev/null 2>&1 || true
    if sudo test -s /etc/pcs/meshtastic.env && sudo test -s /etc/pcs/meshtastic-mqtt.env \
        && sudo grep -q '^PCS_MESHTASTIC_PORT=/dev/ttyACM0$' /etc/pcs/meshtastic.env \
        && sudo grep -Eq '^PCS_MESHTASTIC_MQTT_HOST=.+$' /etc/pcs/meshtastic.env; then
        sudo systemctl enable --now pcs-meshtastic.service
    fi
    sudo systemctl start smbd.service bluetooth.service pcs-control-panel.service >/dev/null 2>&1 || true
    echo "Private PCS identities restored. SSH host keys, Bluetooth state, and Meshtastic transport take full effect after reboot."
    echo "APRS engines remain disabled until hardware/RF validation is recorded again."
fi
