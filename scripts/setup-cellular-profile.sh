#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_CONFIG="${PCS_INSTALL_CONFIG:-${REPO_DIR}/config/pcs-install.conf}"

if [[ -f "${INSTALL_CONFIG}" ]]; then
    # shellcheck source=/dev/null
    source "${INSTALL_CONFIG}"
fi

CONNECTION_NAME="${PCS_CELLULAR_PROFILE:-pcs-cellular-profile}"
CELLULAR_APN="${PCS_CELLULAR_APN:-fast.t-mobile.com}"
ROUTE_METRIC="${PCS_CELLULAR_ROUTE_METRIC:-900}"
FALLBACK_MODE="${PCS_CELLULAR_FALLBACK_MODE:-manual}"
WIFI_INTERFACE="${PCS_WIFI_IFACE:-wlan0}"
POLL_SECONDS="${PCS_CELLULAR_FALLBACK_POLL_SECONDS:-10}"
WIFI_LOSS_SECONDS="${PCS_CELLULAR_FALLBACK_WIFI_LOSS_SECONDS:-30}"
WIFI_RECOVERY_SECONDS="${PCS_CELLULAR_FALLBACK_WIFI_RECOVERY_SECONDS:-30}"
FALLBACK_HELPER_SOURCE="${REPO_DIR}/scripts/pcs_cellular_fallback.py"
FALLBACK_UNIT_SOURCE="${REPO_DIR}/systemd/pcs-cellular-fallback.service"
FALLBACK_HELPER_TARGET="/usr/local/sbin/pcs-cellular-fallback"
FALLBACK_UNIT_TARGET="/etc/systemd/system/pcs-cellular-fallback.service"
FALLBACK_CONFIG_TARGET="/etc/pcs/cellular-fallback.conf"
PERSIST_FALLBACK_MODE=0
CHECK_ONLY=0

usage() {
    cat <<'EOF'
Usage: ./scripts/setup-cellular-profile.sh [options]

Options:
  --fallback MODE  Set cellular policy: manual or wifi-fallback (auto is an alias).
  --check          Report the installed cellular and fallback state without changing it.
  -h, --help       Show this help.

The NetworkManager cellular profile always remains non-autoconnecting. In
wifi-fallback mode, the PCS service explicitly starts cellular after sustained
Wi-Fi loss and disconnects only sessions that it started itself.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fallback)
            [[ $# -ge 2 ]] || { echo "ERROR: --fallback requires a mode." >&2; exit 2; }
            FALLBACK_MODE="$2"
            PERSIST_FALLBACK_MODE=1
            shift 2
            ;;
        --check)
            CHECK_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "${FALLBACK_MODE}" in
    auto|wifi-fallback)
        FALLBACK_MODE="wifi-fallback"
        ;;
    manual)
        ;;
    *)
        echo "ERROR: PCS_CELLULAR_FALLBACK_MODE must be manual or wifi-fallback." >&2
        exit 2
        ;;
esac

for value_name in ROUTE_METRIC POLL_SECONDS WIFI_LOSS_SECONDS WIFI_RECOVERY_SECONDS; do
    value="${!value_name}"
    if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
        echo "ERROR: ${value_name} must be a non-negative integer." >&2
        exit 2
    fi
done

if (( POLL_SECONDS < 1 || POLL_SECONDS > 3600 || WIFI_LOSS_SECONDS > 3600 || WIFI_RECOVERY_SECONDS > 3600 )); then
    echo "ERROR: Fallback timing values must be between 0 and 3600 seconds; poll must be at least 1." >&2
    exit 2
fi

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=()
else
    SUDO=(sudo)
fi

report_state() {
    local autoconnect="missing"
    if command -v nmcli >/dev/null 2>&1 && nmcli -t -f NAME connection show | grep -Fxq -- "${CONNECTION_NAME}"; then
        autoconnect="$(nmcli -g connection.autoconnect connection show "${CONNECTION_NAME}" 2>/dev/null || true)"
    fi

    echo "Cellular profile: ${CONNECTION_NAME}"
    echo "Profile autoconnect: ${autoconnect}"
    echo "Configured fallback mode: ${FALLBACK_MODE}"
    echo "Fallback helper: $([[ -x "${FALLBACK_HELPER_TARGET}" ]] && echo installed || echo missing)"
    echo "Fallback configuration: $([[ -r "${FALLBACK_CONFIG_TARGET}" ]] && echo installed || echo missing)"
    echo "Fallback unit: $([[ -r "${FALLBACK_UNIT_TARGET}" ]] && echo installed || echo missing)"
    if command -v systemctl >/dev/null 2>&1; then
        echo "Fallback service enabled: $(systemctl is-enabled pcs-cellular-fallback.service 2>/dev/null || true)"
        echo "Fallback service active: $(systemctl is-active pcs-cellular-fallback.service 2>/dev/null || true)"
    fi
    if [[ -x "${FALLBACK_HELPER_TARGET}" && -r "${FALLBACK_CONFIG_TARGET}" ]]; then
        "${SUDO[@]}" "${FALLBACK_HELPER_TARGET}" --config "${FALLBACK_CONFIG_TARGET}" --check || true
    fi
}

if (( CHECK_ONLY )); then
    echo
    echo "=== PCS Cellular Profile Check ==="
    echo
    report_state
    exit 0
fi

echo
echo "=== PCS Cellular Profile Setup ==="
echo

if ! command -v nmcli >/dev/null 2>&1; then
    echo "ERROR: nmcli not found. NetworkManager is required."
    exit 1
fi
if [[ ! -f "${FALLBACK_HELPER_SOURCE}" || ! -f "${FALLBACK_UNIT_SOURCE}" ]]; then
    echo "ERROR: Cellular fallback service assets are missing from the repository." >&2
    exit 1
fi

echo "Configuring cellular profile:"
echo "  Name:     ${CONNECTION_NAME}"
echo "  APN:      ${CELLULAR_APN}"
echo "  Policy:   ${FALLBACK_MODE}"
echo "  Wi-Fi:    ${WIFI_INTERFACE}"
echo

if nmcli -t -f NAME connection show | grep -Fxq -- "${CONNECTION_NAME}"; then
    echo "Existing ${CONNECTION_NAME} profile found. Updating it..."
else
    echo "Creating ${CONNECTION_NAME} profile..."
    "${SUDO[@]}" nmcli connection add type gsm ifname "*" con-name "${CONNECTION_NAME}" apn "${CELLULAR_APN}"
fi

"${SUDO[@]}" nmcli connection modify "${CONNECTION_NAME}" gsm.apn "${CELLULAR_APN}" connection.autoconnect no ipv4.method auto ipv6.method auto ipv4.route-metric "${ROUTE_METRIC}" ipv6.route-metric "${ROUTE_METRIC}"

fallback_config="$(mktemp)"
trap 'rm -f "${fallback_config}"' EXIT
cat >"${fallback_config}" <<EOF
[fallback]
wifi_interface=${WIFI_INTERFACE}
cellular_profile=${CONNECTION_NAME}
poll_seconds=${POLL_SECONDS}
wifi_loss_seconds=${WIFI_LOSS_SECONDS}
wifi_recovery_seconds=${WIFI_RECOVERY_SECONDS}
EOF

"${SUDO[@]}" install -d -m 0755 /etc/pcs
"${SUDO[@]}" install -m 0755 "${FALLBACK_HELPER_SOURCE}" "${FALLBACK_HELPER_TARGET}"
"${SUDO[@]}" install -m 0644 "${FALLBACK_UNIT_SOURCE}" "${FALLBACK_UNIT_TARGET}"
"${SUDO[@]}" install -m 0644 "${fallback_config}" "${FALLBACK_CONFIG_TARGET}"
"${SUDO[@]}" systemctl daemon-reload

if [[ "${FALLBACK_MODE}" == "wifi-fallback" ]]; then
    "${SUDO[@]}" systemctl enable pcs-cellular-fallback.service
    "${SUDO[@]}" systemctl restart pcs-cellular-fallback.service
else
    "${SUDO[@]}" systemctl disable --now pcs-cellular-fallback.service 2>/dev/null || true
    # Release only a cellular session bearing the fallback service's ownership marker.
    "${SUDO[@]}" "${FALLBACK_HELPER_TARGET}" --config "${FALLBACK_CONFIG_TARGET}" --release-owned
fi

if (( PERSIST_FALLBACK_MODE )); then
    install_config_dir="$(dirname "${INSTALL_CONFIG}")"
    mkdir -p "${install_config_dir}"
    touch "${INSTALL_CONFIG}"
    if grep -q '^PCS_CELLULAR_FALLBACK_MODE=' "${INSTALL_CONFIG}"; then
        sed -i "s/^PCS_CELLULAR_FALLBACK_MODE=.*/PCS_CELLULAR_FALLBACK_MODE=\"${FALLBACK_MODE}\"/" "${INSTALL_CONFIG}"
    else
        printf '\nPCS_CELLULAR_FALLBACK_MODE="%s"\n' "${FALLBACK_MODE}" >>"${INSTALL_CONFIG}"
    fi
    echo "Saved PCS_CELLULAR_FALLBACK_MODE=${FALLBACK_MODE} in ${INSTALL_CONFIG}."
fi

echo
if [[ "${FALLBACK_MODE}" == "wifi-fallback" ]]; then
    echo "Automatic Wi-Fi-to-cellular fallback is enabled."
    echo "Cellular starts after ${WIFI_LOSS_SECONDS}s without active Wi-Fi and is released after ${WIFI_RECOVERY_SECONDS}s of restored Wi-Fi."
    echo "Only cellular sessions started by the fallback service are automatically disconnected."
else
    echo "Cellular remains under manual control from the PCS Control Panel."
fi
echo "The NetworkManager profile remains non-autoconnecting in both modes."
echo
report_state
