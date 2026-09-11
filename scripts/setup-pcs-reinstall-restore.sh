#!/usr/bin/env bash
set -Eeuo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ ${EUID} -ne 0 && $# -eq 2 ]] || { echo "Run as pi: $0 --network|--api TRUSTED_DECRYPTED_DIRECTORY" >&2; exit 2; }
mode="$1"
directory="$2"
case "${mode}" in
    --network) component=network ;;
    --api) component=api ;;
    *) echo "Unknown recovery phase" >&2; exit 2 ;;
esac
sudo -n true
sudo python3 "${REPO_DIR}/scripts/pcs_reinstall_restore.py" "${component}" "${directory}" --check
if [[ "${component}" == "network" ]]; then
    if systemctl is-active --quiet wg-quick@wg-pcs.service; then
        echo "ERROR: recovery must not replace an active VPN; use a fresh install." >&2
        exit 1
    fi
    sudo python3 "${REPO_DIR}/scripts/pcs_reinstall_restore.py" network "${directory}"
    # Load saved profiles without switching the uplink used by the installer.
    sudo nmcli connection reload
    if sudo test -f "${directory}/etc/pcs/wireguard-management.conf"; then
        bash "${REPO_DIR}/scripts/setup-wireguard-management.sh" --validate-config
    fi
elif sudo test -d "${directory}/etc/pcs-stats-api"; then
    PCS_API_PREPARE_CONFIRM=yes bash "${REPO_DIR}/scripts/setup-pcs-stats-api.sh" --prepare
    sudo python3 "${REPO_DIR}/scripts/pcs_reinstall_restore.py" api "${directory}"
    PCS_API_ACTIVATE_CONFIRM=yes bash "${REPO_DIR}/scripts/setup-pcs-stats-api.sh" --activate
fi
