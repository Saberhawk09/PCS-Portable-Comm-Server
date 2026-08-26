#!/usr/bin/env bash

set -Eeuo pipefail

# Non-login SSH sessions on Raspberry Pi OS may omit /usr/sbin even though
# administrative tools such as nft are installed there.
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export PATH

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_CONFIG_FILE="/etc/pcs/wireguard-management.conf"
CONFIG_FILE="${PCS_WIREGUARD_CONFIG:-${RUNTIME_CONFIG_FILE}}"
WG_INTERFACE="wg-pcs"
WG_CONFIG="/etc/wireguard/${WG_INTERFACE}.conf"
PROFILE_HELPER="${REPO_DIR}/scripts/pcs_wireguard_profile.py"
FIREWALL_SRC="${REPO_DIR}/scripts/pcs-wireguard-firewall.sh"
FIREWALL_DEST="/usr/local/sbin/pcs-wireguard-firewall"
FIREWALL_SERVICE_SRC="${REPO_DIR}/systemd/pcs-wireguard-firewall.service"
FIREWALL_SERVICE_DEST="/etc/systemd/system/pcs-wireguard-firewall.service"
ENDPOINT_REFRESH_SRC="${REPO_DIR}/scripts/pcs-wireguard-endpoint-refresh.sh"
ENDPOINT_REFRESH_DEST="/usr/local/sbin/pcs-wireguard-endpoint-refresh"
ENDPOINT_REFRESH_SERVICE_SRC="${REPO_DIR}/systemd/pcs-wireguard-endpoint-refresh.service"
ENDPOINT_REFRESH_SERVICE_DEST="/etc/systemd/system/pcs-wireguard-endpoint-refresh.service"
ENDPOINT_REFRESH_TIMER_SRC="${REPO_DIR}/systemd/pcs-wireguard-endpoint-refresh.timer"
ENDPOINT_REFRESH_TIMER_DEST="/etc/systemd/system/pcs-wireguard-endpoint-refresh.timer"
DISPATCHER_SRC="${REPO_DIR}/networkmanager/90-pcs-wireguard-firewall"
DISPATCHER_DEST="/etc/NetworkManager/dispatcher.d/90-pcs-wireguard-firewall"
WG_DROPIN_DIR="/etc/systemd/system/wg-quick@${WG_INTERFACE}.service.d"
WG_DROPIN="${WG_DROPIN_DIR}/pcs-management.conf"
MODE="${1:---help}"
SECRET_TEMP_FILES=()

cleanup_secret_temps() {
    local temp_file

    for temp_file in "${SECRET_TEMP_FILES[@]}"; do
        [[ -n "${temp_file}" ]] && rm -f -- "${temp_file}"
    done
}

trap cleanup_secret_temps EXIT

confirm() {
    local prompt="$1"
    local override="${2:-}"
    local answer

    if [[ "${PCS_ASSUME_YES:-}" == "1" || "${override}" == "yes" ]]; then
        answer="yes"
        echo "${prompt} [Y/N] yes"
    else
        read -r -p "${prompt} [Y/N] " answer
    fi

    case "${answer}" in
        y|Y|yes|YES|Yes)
            return 0
            ;;
        *)
            echo "Aborted."
            return 1
            ;;
    esac
}

require_normal_user() {
    if [[ "${EUID}" -eq 0 ]]; then
        echo "ERROR: Do not run this script with sudo." >&2
        echo "Run it as the normal Pi user; individual steps request sudo." >&2
        exit 1
    fi
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: required command not found: $1" >&2
        exit 1
    fi
}

require_sudo() {
    # A deployment account may have command-level NOPASSWD authorization even
    # though `sudo -v` itself still requires a password. Reuse that authorization
    # when available; otherwise perform the normal interactive credential check.
    if ! sudo -n true 2>/dev/null; then
        sudo -v
    fi
}

load_config() {
    if [[ ! -r "${CONFIG_FILE}" ]]; then
        echo "ERROR: WireGuard management config is not readable: ${CONFIG_FILE}" >&2
        echo "Start from config/pcs-wireguard-management.example.conf and keep the runtime copy outside Git." >&2
        exit 1
    fi

    # shellcheck source=/dev/null
    source "${CONFIG_FILE}"

    PCS_WG_INTERFACE="${PCS_WG_INTERFACE:-wg-pcs}"
    PCS_WG_LAN_INTERFACE="${PCS_WG_LAN_INTERFACE:-eth0}"
    PCS_WG_LAN_NETWORK="${PCS_WG_LAN_NETWORK:-10.42.0.0/24}"
    PCS_WG_PERSISTENT_KEEPALIVE="${PCS_WG_PERSISTENT_KEEPALIVE:-25}"
    PCS_WG_PRIVATE_KEY_FILE="${PCS_WG_PRIVATE_KEY_FILE:-/etc/pcs/wireguard/private.key}"
    PCS_WG_USE_PRESHARED_KEY="${PCS_WG_USE_PRESHARED_KEY:-no}"
    PCS_WG_PRESHARED_KEY_FILE="${PCS_WG_PRESHARED_KEY_FILE:-/etc/pcs/wireguard/preshared.key}"
}

validate_config() {
    load_config
    require_command python3

    if [[ ! -x "${FIREWALL_SRC}" && ! -x "${FIREWALL_DEST}" ]]; then
        echo "ERROR: PCS WireGuard firewall validator is missing." >&2
        exit 1
    fi

    validator="${FIREWALL_SRC}"
    if [[ ! -x "${validator}" ]]; then
        validator="${FIREWALL_DEST}"
    fi
    PCS_WIREGUARD_CONFIG="${CONFIG_FILE}" "${validator}" --validate-config

    python3 - \
        "${PCS_WG_HUB_PUBLIC_KEY:-}" \
        "${PCS_WG_ENDPOINT:-}" \
        "${PCS_WG_PERSISTENT_KEEPALIVE}" \
        "${PCS_WG_INTERFACE}" \
        "${PCS_WG_LAN_INTERFACE}" \
        "${PCS_WG_LAN_NETWORK}" \
        "${PCS_WG_PRIVATE_KEY_FILE}" \
        "${PCS_WG_USE_PRESHARED_KEY}" \
        "${PCS_WG_PRESHARED_KEY_FILE}" <<'PY'
import base64
import binascii
import sys

(
    public_key,
    endpoint,
    keepalive,
    wg_interface,
    lan_interface,
    lan_network,
    private_key_file,
    use_preshared_key,
    preshared_key_file,
) = sys.argv[1:]

if public_key in {"", "CHANGE_ME"}:
    raise SystemExit("ERROR: set the home hub public key before configuration")
try:
    decoded = base64.b64decode(public_key, validate=True)
except (binascii.Error, ValueError):
    raise SystemExit("ERROR: home hub public key is not valid base64")
if len(decoded) != 32:
    raise SystemExit("ERROR: home hub public key must decode to 32 bytes")

if endpoint.count(":") != 1:
    raise SystemExit("ERROR: endpoint must use hostname-or-IPv4:port syntax")
host, port_text = endpoint.rsplit(":", 1)
if not host or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
    raise SystemExit("ERROR: invalid WireGuard endpoint")

if not keepalive.isdigit() or not 1 <= int(keepalive) <= 65535:
    raise SystemExit("ERROR: persistent keepalive must be 1-65535 seconds")
if wg_interface != "wg-pcs":
    raise SystemExit("ERROR: PCS management uses the fixed wg-pcs interface")
if lan_interface != "eth0" or lan_network != "10.42.0.0/24":
    raise SystemExit("ERROR: WireGuard config does not match the commissioned PCS LAN topology")
if private_key_file != "/etc/pcs/wireguard/private.key":
    raise SystemExit("ERROR: PCS WireGuard private key must use the fixed root-only path")
if use_preshared_key not in {"yes", "no"}:
    raise SystemExit("ERROR: PCS_WG_USE_PRESHARED_KEY must be yes or no")
if preshared_key_file != "/etc/pcs/wireguard/preshared.key":
    raise SystemExit("ERROR: PCS WireGuard pre-shared key must use the fixed root-only path")
PY

    echo "PCS WireGuard management configuration is valid."
}

validate_private_key() {
    local private_key
    local key_mode

    require_command wg
    if ! sudo test -f "${PCS_WG_PRIVATE_KEY_FILE}"; then
        echo "ERROR: private key not found: ${PCS_WG_PRIVATE_KEY_FILE}" >&2
        echo "Run --generate-key first." >&2
        exit 1
    fi

    key_mode="$(sudo stat -c '%a' "${PCS_WG_PRIVATE_KEY_FILE}")"
    if [[ "${key_mode}" != "600" && "${key_mode}" != "400" ]]; then
        echo "ERROR: private key permissions must be 0600 or 0400, found ${key_mode}." >&2
        exit 1
    fi

    private_key="$(sudo sed -n '1p' "${PCS_WG_PRIVATE_KEY_FILE}")"
    if [[ -z "${private_key}" ]] || ! printf '%s\n' "${private_key}" | wg pubkey >/dev/null 2>&1; then
        echo "ERROR: private key file does not contain a valid WireGuard key." >&2
        exit 1
    fi
}

validate_preshared_key() {
    local key_mode

    if [[ "${PCS_WG_USE_PRESHARED_KEY}" != "yes" ]]; then
        return 0
    fi
    require_command python3
    if ! sudo test -f "${PCS_WG_PRESHARED_KEY_FILE}"; then
        echo "ERROR: configured WireGuard pre-shared key is missing: ${PCS_WG_PRESHARED_KEY_FILE}" >&2
        exit 1
    fi
    key_mode="$(sudo stat -c '%a' "${PCS_WG_PRESHARED_KEY_FILE}")"
    if [[ "${key_mode}" != "600" && "${key_mode}" != "400" ]]; then
        echo "ERROR: pre-shared key permissions must be 0600 or 0400, found ${key_mode}." >&2
        exit 1
    fi
    if ! sudo sed -n '1p' "${PCS_WG_PRESHARED_KEY_FILE}" | python3 -c '
import base64
import binascii
import sys
value = sys.stdin.read().strip()
try:
    decoded = base64.b64decode(value, validate=True)
except (binascii.Error, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if len(decoded) == 32 else 1)
'; then
        echo "ERROR: pre-shared key file does not contain a valid 32-byte WireGuard key." >&2
        exit 1
    fi
}

validate_profile_file() {
    local profile_path="$1"
    local profile_mode
    local profile_owner

    require_normal_user
    require_command python3
    if [[ -z "${profile_path}" ]]; then
        echo "ERROR: provide the path to a wg-quick profile." >&2
        exit 2
    fi
    if [[ ! -f "${profile_path}" || -L "${profile_path}" ]]; then
        echo "ERROR: WireGuard profile must be a regular, non-symlink file: ${profile_path}" >&2
        exit 1
    fi
    if [[ ! -f "${PROFILE_HELPER}" ]]; then
        echo "ERROR: WireGuard profile validator is missing: ${PROFILE_HELPER}" >&2
        exit 1
    fi

    profile_mode="$(stat -c '%a' -- "${profile_path}")"
    profile_owner="$(stat -c '%u' -- "${profile_path}")"
    if [[ "${profile_owner}" != "${EUID}" ]]; then
        echo "ERROR: WireGuard profile must be owned by the user running this installer." >&2
        exit 1
    fi
    if [[ "${profile_mode}" != "600" && "${profile_mode}" != "400" ]]; then
        echo "ERROR: WireGuard profile permissions must be 0600 or 0400, found ${profile_mode}." >&2
        echo "Run: chmod 600 '${profile_path}'" >&2
        exit 1
    fi

    python3 "${PROFILE_HELPER}" --validate "${profile_path}"
}

import_profile() {
    local profile_path="$1"
    local policy_temp
    local key_temp
    local preshared_key_temp
    local original_config
    local credentials_change="no"

    validate_profile_file "${profile_path}"
    require_command wg

    if systemctl is-active --quiet "wg-quick@${WG_INTERFACE}.service" \
        || systemctl is-enabled --quiet "wg-quick@${WG_INTERFACE}.service" \
        || systemctl is-active --quiet pcs-wireguard-firewall.service \
        || systemctl is-enabled --quiet pcs-wireguard-firewall.service; then
        echo "ERROR: deactivate WireGuard management before importing a replacement profile." >&2
        exit 1
    fi
    if [[ ! -x "${FIREWALL_DEST}" || ! -f "${FIREWALL_SERVICE_DEST}" || ! -x "${DISPATCHER_DEST}" ]]; then
        echo "ERROR: inactive feature components are not prepared; run --prepare first." >&2
        exit 1
    fi

    policy_temp="$(mktemp)"
    key_temp="$(mktemp)"
    preshared_key_temp="$(mktemp)"
    SECRET_TEMP_FILES+=("${policy_temp}" "${key_temp}" "${preshared_key_temp}")
    umask 077
    python3 "${PROFILE_HELPER}" --render \
        "${profile_path}" "${policy_temp}" "${key_temp}" "${preshared_key_temp}"

    if ! wg pubkey <"${key_temp}" >/dev/null 2>&1; then
        echo "ERROR: imported profile contains a private key rejected by wg." >&2
        exit 1
    fi

    original_config="${CONFIG_FILE}"
    CONFIG_FILE="${policy_temp}"
    validate_config
    CONFIG_FILE="${original_config}"

    require_sudo
    if sudo test -f /etc/pcs/wireguard/private.key \
        && ! sudo cmp -s -- /etc/pcs/wireguard/private.key "${key_temp}"; then
        credentials_change="yes"
    fi
    if sudo test -f /etc/pcs/wireguard/preshared.key \
        && { [[ ! -s "${preshared_key_temp}" ]] \
            || ! sudo cmp -s -- /etc/pcs/wireguard/preshared.key "${preshared_key_temp}"; }; then
        credentials_change="yes"
    fi
    if [[ "${credentials_change}" == "yes" ]]; then
        echo "The imported profile changes inactive PCS WireGuard credential material."
        confirm "Replace the inactive PCS WireGuard credentials?" "${PCS_WIREGUARD_IMPORT_REPLACE_CONFIRM:-}" || exit 1
    fi

    sudo install -d -o root -g root -m 0755 /etc/pcs
    sudo install -d -o root -g root -m 0700 /etc/pcs/wireguard
    sudo install -o root -g root -m 0644 "${policy_temp}" "${RUNTIME_CONFIG_FILE}"
    sudo install -o root -g root -m 0600 "${key_temp}" /etc/pcs/wireguard/private.key
    if [[ -s "${preshared_key_temp}" ]]; then
        sudo install -o root -g root -m 0600 \
            "${preshared_key_temp}" /etc/pcs/wireguard/preshared.key
        echo "Imported the ASUS pre-shared key into a separate root-only file."
    else
        sudo rm -f -- /etc/pcs/wireguard/preshared.key
    fi

    echo "Imported the restricted profile into root-owned PCS runtime files."
    echo "Any validated ASUS DNS entry was deliberately not applied."
    echo "PCS public key (safe to compare with the home hub peer):"
    wg pubkey <"${key_temp}"
    echo "The tunnel and firewall remain disabled and inactive until --activate succeeds."
}

prepare_feature() {
    local required_file

    require_normal_user
    if systemctl is-active --quiet "wg-quick@${WG_INTERFACE}.service" \
        || systemctl is-enabled --quiet "wg-quick@${WG_INTERFACE}.service" \
        || systemctl is-active --quiet pcs-wireguard-firewall.service \
        || systemctl is-enabled --quiet pcs-wireguard-firewall.service; then
        echo "ERROR: deactivate WireGuard management before replacing installed feature files." >&2
        exit 1
    fi
    for required_file in \
        "${FIREWALL_SRC}" \
        "${FIREWALL_SERVICE_SRC}" \
        "${ENDPOINT_REFRESH_SRC}" \
        "${ENDPOINT_REFRESH_SERVICE_SRC}" \
        "${ENDPOINT_REFRESH_TIMER_SRC}" \
        "${DISPATCHER_SRC}"; do
        if [[ ! -f "${required_file}" ]]; then
            echo "ERROR: required repository file not found: ${required_file}" >&2
            exit 1
        fi
    done

    echo "This installs WireGuard tooling and inactive PCS management components."
    echo "It does not create a tunnel, enable a service, or alter the running firewall."
    confirm "Prepare the inactive WireGuard management feature?" "${PCS_WIREGUARD_PREPARE_CONFIRM:-}" || exit 0

    require_sudo
    sudo apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y wireguard-tools nftables
    sudo install -d -o root -g root -m 0700 /etc/pcs/wireguard
    sudo install -d -o root -g root -m 0700 /etc/wireguard
    sudo install -o root -g root -m 0755 "${FIREWALL_SRC}" "${FIREWALL_DEST}"
    sudo install -o root -g root -m 0644 "${FIREWALL_SERVICE_SRC}" "${FIREWALL_SERVICE_DEST}"
    sudo install -o root -g root -m 0755 "${ENDPOINT_REFRESH_SRC}" "${ENDPOINT_REFRESH_DEST}"
    sudo install -o root -g root -m 0644 "${ENDPOINT_REFRESH_SERVICE_SRC}" "${ENDPOINT_REFRESH_SERVICE_DEST}"
    sudo install -o root -g root -m 0644 "${ENDPOINT_REFRESH_TIMER_SRC}" "${ENDPOINT_REFRESH_TIMER_DEST}"
    sudo install -o root -g root -m 0755 "${DISPATCHER_SRC}" "${DISPATCHER_DEST}"
    sudo systemctl daemon-reload

    echo "WireGuard management components are prepared but disabled and inactive."
    echo "Next: create ${CONFIG_FILE}, then run --generate-key and exchange the printed public key with the home hub."
}

generate_key() {
    local key_temp

    require_normal_user
    require_command wg
    load_config
    require_sudo

    if sudo test -e "${PCS_WG_PRIVATE_KEY_FILE}"; then
        echo "Existing private key preserved: ${PCS_WG_PRIVATE_KEY_FILE}"
    else
        key_temp="$(mktemp)"
        SECRET_TEMP_FILES+=("${key_temp}")
        umask 077
        wg genkey >"${key_temp}"
        sudo install -d -o root -g root -m 0700 "$(dirname "${PCS_WG_PRIVATE_KEY_FILE}")"
        sudo install -o root -g root -m 0600 "${key_temp}" "${PCS_WG_PRIVATE_KEY_FILE}"
        rm -f -- "${key_temp}"
        echo "Generated a new PCS WireGuard private key outside the repository."
    fi

    validate_private_key
    echo "PCS public key (safe to add to the home hub peer configuration):"
    sudo sed -n '1p' "${PCS_WG_PRIVATE_KEY_FILE}" | wg pubkey
}

configure_feature() {
    local private_key
    local preshared_key=""
    local preshared_key_line=""
    local config_temp
    local dropin_temp

    require_normal_user
    require_sudo
    validate_config
    validate_private_key
    validate_preshared_key

    if systemctl is-active --quiet "wg-quick@${WG_INTERFACE}.service" \
        || systemctl is-enabled --quiet "wg-quick@${WG_INTERFACE}.service" \
        || systemctl is-active --quiet pcs-wireguard-firewall.service \
        || systemctl is-enabled --quiet pcs-wireguard-firewall.service; then
        echo "ERROR: deactivate the existing WireGuard feature services before replacing runtime configuration." >&2
        exit 1
    fi
    if [[ ! -x "${FIREWALL_DEST}" \
        || ! -f "${FIREWALL_SERVICE_DEST}" \
        || ! -x "${ENDPOINT_REFRESH_DEST}" \
        || ! -f "${ENDPOINT_REFRESH_SERVICE_DEST}" \
        || ! -f "${ENDPOINT_REFRESH_TIMER_DEST}" \
        || ! -x "${DISPATCHER_DEST}" ]]; then
        echo "ERROR: inactive feature components are not prepared; run --prepare first." >&2
        exit 1
    fi

    private_key="$(sudo sed -n '1p' "${PCS_WG_PRIVATE_KEY_FILE}")"
    if [[ "${PCS_WG_USE_PRESHARED_KEY}" == "yes" ]]; then
        preshared_key="$(sudo sed -n '1p' "${PCS_WG_PRESHARED_KEY_FILE}")"
        preshared_key_line="PresharedKey = ${preshared_key}"
    fi
    config_temp="$(mktemp)"
    dropin_temp="$(mktemp)"
    SECRET_TEMP_FILES+=("${config_temp}" "${dropin_temp}")
    umask 077

    cat >"${config_temp}" <<EOF
[Interface]
Address = ${PCS_WG_ADDRESS}
PrivateKey = ${private_key}
Table = auto

[Peer]
PublicKey = ${PCS_WG_HUB_PUBLIC_KEY}
${preshared_key_line}
Endpoint = ${PCS_WG_ENDPOINT}
AllowedIPs = ${PCS_WG_ALLOWED_IPS}
PersistentKeepalive = ${PCS_WG_PERSISTENT_KEEPALIVE}
EOF

    cat >"${dropin_temp}" <<EOF
[Unit]
Requires=pcs-wireguard-firewall.service
After=pcs-wireguard-firewall.service network-online.target
Wants=network-online.target
EOF

    sudo install -o root -g root -m 0600 "${config_temp}" "${WG_CONFIG}"
    sudo install -d -o root -g root -m 0755 "${WG_DROPIN_DIR}"
    sudo install -o root -g root -m 0644 "${dropin_temp}" "${WG_DROPIN}"
    rm -f -- "${config_temp}" "${dropin_temp}"
    unset private_key preshared_key preshared_key_line
    sudo systemctl daemon-reload

    echo "Rendered ${WG_CONFIG} with mode 0600."
    echo "The tunnel and firewall remain disabled and inactive."
}

deactivate_feature() {
    require_normal_user
    require_sudo
    sudo systemctl disable --now pcs-wireguard-endpoint-refresh.timer 2>/dev/null || true
    sudo systemctl stop pcs-wireguard-endpoint-refresh.service 2>/dev/null || true
    sudo systemctl disable --now "wg-quick@${WG_INTERFACE}.service" 2>/dev/null || true
    sudo systemctl disable --now pcs-wireguard-firewall.service 2>/dev/null || true
    if [[ -x "${FIREWALL_DEST}" && -r "${CONFIG_FILE}" ]]; then
        sudo env PCS_WIREGUARD_CONFIG="${CONFIG_FILE}" "${FIREWALL_DEST}" --clear || true
    fi
    echo "PCS WireGuard management is disabled and inactive; runtime config and key material are preserved."
}

check_no_default_route() {
    if ip -4 route show default dev "${WG_INTERFACE}" | grep -q .; then
        echo "ERROR: WireGuard installed an IPv4 default route." >&2
        return 1
    fi
    if ip -6 route show default dev "${WG_INTERFACE}" 2>/dev/null | grep -q .; then
        echo "ERROR: WireGuard installed an IPv6 default route." >&2
        return 1
    fi
    echo "No default route uses ${WG_INTERFACE}."
}

check_handshake() {
    local latest
    local now
    local age

    latest="$(sudo wg show "${WG_INTERFACE}" latest-handshakes | awk 'NR == 1 { print $2 }')"
    if [[ -z "${latest}" || "${latest}" == "0" ]]; then
        return 1
    fi
    now="$(date +%s)"
    age=$((now - latest))
    if (( age > 180 )); then
        echo "WARNING: latest WireGuard handshake is ${age} seconds old."
    else
        echo "WireGuard handshake is current (${age} seconds old)."
    fi
}

activate_feature() {
    local timeout
    local waited=0

    require_normal_user
    configure_feature

    echo "Activation changes the running firewall and starts the outbound management tunnel."
    echo "It does not start Wi-Fi or cellular and it cannot install a default route."
    confirm "Activate PCS WireGuard management now?" "${PCS_WIREGUARD_ACTIVATE_CONFIRM:-}" || exit 0

    if ! sudo systemctl enable \
        pcs-wireguard-firewall.service \
        "wg-quick@${WG_INTERFACE}.service" \
        pcs-wireguard-endpoint-refresh.timer; then
        echo "ERROR: could not enable the WireGuard management services; rolling activation back." >&2
        deactivate_feature
        exit 1
    fi
    if ! sudo systemctl start pcs-wireguard-firewall.service; then
        echo "ERROR: isolation firewall did not start; rolling activation back." >&2
        deactivate_feature
        exit 1
    fi
    if ! sudo systemctl start "wg-quick@${WG_INTERFACE}.service"; then
        echo "ERROR: WireGuard tunnel did not start; rolling activation back." >&2
        deactivate_feature
        exit 1
    fi
    if ! sudo systemctl start pcs-wireguard-endpoint-refresh.service \
        || ! sudo systemctl start pcs-wireguard-endpoint-refresh.timer; then
        echo "ERROR: WireGuard IPv4 endpoint refresh did not start; rolling activation back." >&2
        deactivate_feature
        exit 1
    fi

    if ! check_no_default_route; then
        deactivate_feature
        exit 1
    fi

    timeout="${PCS_WG_HANDSHAKE_TIMEOUT:-45}"
    if [[ ! "${timeout}" =~ ^[0-9]+$ ]] || (( timeout < 5 || timeout > 55 )); then
        echo "ERROR: PCS_WG_HANDSHAKE_TIMEOUT must be 5-55 seconds." >&2
        deactivate_feature
        exit 1
    fi

    echo "Waiting up to ${timeout} seconds for an authenticated home-hub handshake..."
    while (( waited < timeout )); do
        if check_handshake; then
            sudo env PCS_WIREGUARD_CONFIG="${CONFIG_FILE}" "${FIREWALL_DEST}" --check
            echo "PCS WireGuard management activation passed its local safety checks."
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done

    echo "ERROR: no authenticated WireGuard handshake was observed; rolling activation back." >&2
    deactivate_feature
    exit 1
}

check_feature() {
    local config_mode
    local allowed_live
    local allowed_expected

    require_normal_user
    validate_config
    validate_private_key
    validate_preshared_key

    if ! sudo test -f "${WG_CONFIG}"; then
        echo "ERROR: rendered WireGuard config is missing: ${WG_CONFIG}" >&2
        exit 1
    fi
    config_mode="$(sudo stat -c '%a' "${WG_CONFIG}")"
    if [[ "${config_mode}" != "600" ]]; then
        echo "ERROR: ${WG_CONFIG} must have mode 0600, found ${config_mode}." >&2
        exit 1
    fi
    systemctl is-enabled --quiet pcs-wireguard-firewall.service
    systemctl is-active --quiet pcs-wireguard-firewall.service
    systemctl is-enabled --quiet "wg-quick@${WG_INTERFACE}.service"
    systemctl is-active --quiet "wg-quick@${WG_INTERFACE}.service"
    systemctl is-enabled --quiet pcs-wireguard-endpoint-refresh.timer
    systemctl is-active --quiet pcs-wireguard-endpoint-refresh.timer

    sudo env PCS_WIREGUARD_CONFIG="${CONFIG_FILE}" "${FIREWALL_DEST}" --check
    sudo wg show "${WG_INTERFACE}" >/dev/null
    check_no_default_route

    allowed_live="$(sudo wg show "${WG_INTERFACE}" allowed-ips | awk '{$1=""; sub(/^ /, ""); print}' | tr ' ' ',' | tr -d '\n')"
    allowed_expected="${PCS_WG_ALLOWED_IPS//[[:space:]]/}"
    if [[ "${allowed_live}" != "${allowed_expected}" ]]; then
        echo "ERROR: live WireGuard AllowedIPs differ from the approved /32 routes." >&2
        exit 1
    fi

    if ! check_handshake; then
        echo "WARNING: tunnel is configured and active but has no recorded handshake (WAN or hub may be unavailable)."
    fi
    echo "PCS WireGuard management checks passed."
}

rollback_feature() {
    require_normal_user
    echo "Rollback disables the tunnel and removes installed feature/runtime files."
    echo "The private key, optional pre-shared key, and deployment-local ${CONFIG_FILE} are deliberately preserved."
    confirm "Roll back PCS WireGuard management?" "${PCS_WIREGUARD_ROLLBACK_CONFIRM:-}" || exit 0

    deactivate_feature
    sudo rm -f -- \
        "${WG_CONFIG}" \
        "${WG_DROPIN}" \
        "${DISPATCHER_DEST}" \
        "${ENDPOINT_REFRESH_TIMER_DEST}" \
        "${ENDPOINT_REFRESH_SERVICE_DEST}" \
        "${ENDPOINT_REFRESH_DEST}" \
        "${FIREWALL_SERVICE_DEST}" \
        "${FIREWALL_DEST}"
    sudo rmdir --ignore-fail-on-non-empty "${WG_DROPIN_DIR}" 2>/dev/null || true
    sudo systemctl daemon-reload
    echo "WireGuard management feature files were removed; key material and local input config remain available for recovery."
}

show_help() {
    cat <<'EOF'
Usage: setup-wireguard-management.sh COMMAND

Commands:
  --prepare          Install inactive tools, firewall/endpoint helpers and units
  --validate-profile PROFILE  Validate a restricted wg-quick profile without importing it
  --import-profile PROFILE    Import into protected PCS runtime files; stay inactive
  --generate-key     Create the PCS private key if absent; print public key
  --validate-config  Validate split-tunnel and trust-boundary inputs
  --configure        Render root-only wg-quick config; keep everything inactive
  --activate         Configure, enable, start, require handshake, verify isolation
  --check            Verify active services, routes, firewall, and tunnel state
  --deactivate       Disable tunnel/firewall but preserve config and key
  --rollback         Deactivate and remove feature files; preserve config and key
  -h, --help         Show this help

No command contacts or changes the home WireGuard hub. There is no arbitrary
remote-command endpoint, default route, home-LAN route, or cellular auto-start.
ASUS DNS exports are validated but not applied; optional pre-shared keys remain
in separate root-only files and are never printed.
EOF
}

case "${MODE}" in
    --prepare)
        prepare_feature
        ;;
    --validate-profile)
        validate_profile_file "${2:-}"
        ;;
    --import-profile)
        import_profile "${2:-}"
        ;;
    --generate-key)
        generate_key
        ;;
    --validate-config)
        validate_config
        ;;
    --configure)
        configure_feature
        ;;
    --activate)
        activate_feature
        ;;
    --check)
        check_feature
        ;;
    --deactivate)
        deactivate_feature
        ;;
    --rollback)
        rollback_feature
        ;;
    -h|--help)
        show_help
        ;;
    *)
        show_help >&2
        exit 2
        ;;
esac
