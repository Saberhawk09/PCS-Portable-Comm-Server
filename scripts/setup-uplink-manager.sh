#!/usr/bin/env bash
set -Eeuo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_CONFIG="${PCS_INSTALL_CONFIG:-${REPO_DIR}/config/pcs-install.conf}"
if [[ -f "${INSTALL_CONFIG}" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${INSTALL_CONFIG}"
    set +a
fi
if [[ "${EUID}" -ne 0 && " ${*} " != *" --check "* && " ${*} " != *" --list "* && " ${*} " != *" --help "* ]]; then
    exec sudo --preserve-env=PCS_UPLINK_MODE,PCS_UPLINK_PRIORITY,PCS_STARLINK_IFACE,PCS_STARLINK_MAC,PCS_STARLINK_PROFILE,PCS_WIFI_IFACE,PCS_CELLULAR_PROFILE,PCS_CELLULAR_ROUTE_METRIC,PCS_CELLULAR_FALLBACK_MODE,PCS_CELLULAR_FALLBACK_POLL_SECONDS,PCS_CELLULAR_FALLBACK_WIFI_LOSS_SECONDS,PCS_CELLULAR_FALLBACK_WIFI_RECOVERY_SECONDS \
        python3 "${REPO_DIR}/scripts/pcs_uplink_setup.py" "$@"
fi
exec python3 "${REPO_DIR}/scripts/pcs_uplink_setup.py" "$@"
