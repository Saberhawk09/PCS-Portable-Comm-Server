#!/usr/bin/env bash

set -Eeuo pipefail

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export PATH

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MODE="${1:---help}"

API_USER="pcs-api"
API_GROUP="pcs-api"
CONFIG_DIR="/etc/pcs-stats-api"
POLICY_FILE="${CONFIG_DIR}/policy.conf"
RUNTIME_ENV="${CONFIG_DIR}/runtime.env"
TOKEN_FILE="${CONFIG_DIR}/api-read-tokens.json"
TLS_DIR="${CONFIG_DIR}/tls"
CERT_FILE="${TLS_DIR}/server.crt"
KEY_FILE="${TLS_DIR}/server.key"
APPLICATION_SRC="${REPO_DIR}/web/pcs-control-panel/pcs_stats_api.py"
APPLICATION_DIR="/usr/local/lib/pcs-stats-api"
APPLICATION_DST="${APPLICATION_DIR}/pcs_stats_api.py"
SETUP_DST="/usr/local/sbin/pcs-stats-api-setup"
TOKEN_HELPER_SRC="${SCRIPT_DIR}/pcs_api_token.py"
TOKEN_HELPER_DST="/usr/local/sbin/pcs-api-token"
# Installed and owned by setup-pcs-control-panel.sh; the Stats API consumes
# this exact helper and never removes it during rollback.
PASSWORD_HELPER_DST="/usr/local/sbin/pcs-admin-password-helper"
BACKUP_CONFIG_HELPER_SRC="${SCRIPT_DIR}/pcs_backup_config.py"
BACKUP_CONFIG_HELPER_DST="/usr/local/sbin/pcs-backup-config"
FIREWALL_SRC="${SCRIPT_DIR}/pcs-stats-api-firewall.sh"
FIREWALL_DST="/usr/local/sbin/pcs-stats-api-firewall"
SERVICE_SRC="${REPO_DIR}/systemd/pcs-stats-api.service"
SERVICE_DST="/etc/systemd/system/pcs-stats-api.service"
FIREWALL_SERVICE_SRC="${REPO_DIR}/systemd/pcs-stats-api-firewall.service"
FIREWALL_SERVICE_DST="/etc/systemd/system/pcs-stats-api-firewall.service"
SUDOERS_FILE="/etc/sudoers.d/pcs-stats-api"
SUDOERS_TEMP=""

cleanup() {
    if [[ -n "${SUDOERS_TEMP}" && -f "${SUDOERS_TEMP}" ]]; then
        rm -f -- "${SUDOERS_TEMP}"
    fi
}
trap cleanup EXIT

require_normal_user() {
    if [[ "${EUID}" -eq 0 ]]; then
        echo "ERROR: run this script as the normal Pi user, not with sudo." >&2
        exit 1
    fi
}

require_sudo() {
    if ! sudo -n true 2>/dev/null; then
        if [[ ! -t 0 ]]; then
            echo "ERROR: sudo credentials are required in an interactive terminal." >&2
            exit 1
        fi
        sudo -v
    fi
}

confirm() {
    local prompt="$1"
    local override="$2"
    local answer
    if [[ "${!override:-}" == "yes" ]]; then
        return 0
    fi
    if [[ ! -t 0 ]]; then
        echo "ERROR: confirmation required; set ${override}=yes after reviewing the command." >&2
        return 1
    fi
    read -r -p "${prompt} [y/N] " answer
    [[ "${answer}" =~ ^([yY]|[yY][eE][sS])$ ]]
}

require_prepared_sources() {
    local path
    for path in \
        "${APPLICATION_SRC}" \
        "${TOKEN_HELPER_SRC}" \
        "${BACKUP_CONFIG_HELPER_SRC}" \
        "${FIREWALL_SRC}" \
        "${SERVICE_SRC}" \
        "${FIREWALL_SERVICE_SRC}"; do
        [[ -f "${path}" ]] || {
            echo "ERROR: missing API source file: ${path}" >&2
            exit 1
        }
    done
}

validate_policy_syntax() {
    local policy="$1"
    [[ -f "${policy}" && ! -L "${policy}" ]] || {
        echo "ERROR: API policy must be a regular non-symlink file: ${policy}" >&2
        return 1
    }
    python3 - "${policy}" <<'PY'
import re
import sys
from pathlib import Path

allowed = {
    "PCS_API_PORT",
    "PCS_API_ALLOWED_INTERFACE_SOURCES",
    "PCS_API_CERT_FILE",
    "PCS_API_KEY_FILE",
    "PCS_API_TOKEN_FILE",
    "PCS_API_CERTIFICATE_IDENTITIES",
    "PCS_API_DISPATCHER",
    "PCS_API_APPLICATION",
}
required = set(allowed)
seen = set()
line_pattern = re.compile(r'^([A-Z0-9_]+)="([^"`$\\]*)"$')
for number, raw in enumerate(Path(sys.argv[1]).read_text(encoding="utf-8").splitlines(), 1):
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    match = line_pattern.fullmatch(line)
    if not match:
        raise SystemExit(f"ERROR: unsafe policy syntax on line {number}")
    key, value = match.groups()
    if key not in allowed or key in seen or not value:
        raise SystemExit(f"ERROR: unsupported, duplicate, or empty policy key on line {number}: {key}")
    seen.add(key)
missing = sorted(required - seen)
if missing:
    raise SystemExit("ERROR: API policy is missing: " + ", ".join(missing))
PY
}

load_policy() {
    local policy="$1"
    local firewall_validator="${FIREWALL_SRC}"
    validate_policy_syntax "${policy}"
    # shellcheck source=/dev/null
    source "${policy}"
    [[ "${PCS_API_CERT_FILE}" == "${CERT_FILE}" ]] || {
        echo "ERROR: certificate path must be ${CERT_FILE}" >&2
        return 1
    }
    [[ "${PCS_API_KEY_FILE}" == "${KEY_FILE}" ]] || {
        echo "ERROR: key path must be ${KEY_FILE}" >&2
        return 1
    }
    [[ "${PCS_API_TOKEN_FILE}" == "${TOKEN_FILE}" ]] || {
        echo "ERROR: token path must be ${TOKEN_FILE}" >&2
        return 1
    }
    [[ "${PCS_API_DISPATCHER}" == "/usr/local/sbin/pcs-web-action" ]] || {
        echo "ERROR: dispatcher path is fixed." >&2
        return 1
    }
    [[ "${PCS_API_APPLICATION}" == "${APPLICATION_DST}" ]] || {
        echo "ERROR: application path is fixed." >&2
        return 1
    }
    if [[ ! -x "${firewall_validator}" && -x "${FIREWALL_DST}" ]]; then
        firewall_validator="${FIREWALL_DST}"
    fi
    PCS_STATS_API_CONFIG="${policy}" "${firewall_validator}" --validate-config
}

validate_tls_pair() {
    local certificate="$1"
    local key="$2"
    local runtime_identity="${3:-no}"
    local temp_dir
    local identity
    local value
    local validation_output
    local -a openssl_command

    if [[ "${runtime_identity}" == "yes" ]]; then
        openssl_command=(sudo -u "${API_USER}" openssl)
    else
        openssl_command=(openssl)
    fi

    if [[ "${runtime_identity}" == "yes" ]]; then
        sudo -u "${API_USER}" test -f "${certificate}" \
            && sudo test ! -L "${certificate}"
    else
        [[ -f "${certificate}" && ! -L "${certificate}" ]]
    fi || {
        echo "ERROR: TLS certificate must be a regular non-symlink file." >&2
        return 1
    }
    if [[ "${runtime_identity}" == "yes" ]]; then
        sudo -u "${API_USER}" test -f "${key}" \
            && sudo test ! -L "${key}"
    else
        [[ -f "${key}" && ! -L "${key}" ]]
    fi || {
        echo "ERROR: TLS key must be a regular non-symlink file." >&2
        return 1
    }
    "${openssl_command[@]}" x509 -in "${certificate}" -noout -checkend 86400 >/dev/null || {
        echo "ERROR: TLS certificate is invalid or expires within 24 hours." >&2
        return 1
    }
    "${openssl_command[@]}" pkey -in "${key}" -noout -check >/dev/null
    temp_dir="$(mktemp -d)"
    "${openssl_command[@]}" x509 -in "${certificate}" -pubkey -noout >"${temp_dir}/certificate.pub"
    "${openssl_command[@]}" pkey -in "${key}" -pubout >"${temp_dir}/key.pub"
    if ! cmp -s "${temp_dir}/certificate.pub" "${temp_dir}/key.pub"; then
        rm -rf -- "${temp_dir}"
        echo "ERROR: TLS certificate and private key do not match." >&2
        return 1
    fi
    rm -rf -- "${temp_dir}"

    IFS=',' read -r -a identities <<<"${PCS_API_CERTIFICATE_IDENTITIES}"
    for identity in "${identities[@]}"; do
        identity="${identity//[[:space:]]/}"
        value="${identity#*:}"
        case "${identity}" in
            IP:*)
                # Some OpenSSL builds print a mismatch but still exit zero.
                if ! validation_output="$(LC_ALL=C "${openssl_command[@]}" x509 -in "${certificate}" -noout -checkip "${value}" 2>&1)" \
                    || [[ "${validation_output}" != *"does match certificate"* ]]; then
                    echo "ERROR: TLS certificate SAN is missing required IP identity: ${value}" >&2
                    return 1
                fi
                ;;
            DNS:*)
                # Require the explicit positive result as well as command success.
                if ! validation_output="$(LC_ALL=C "${openssl_command[@]}" x509 -in "${certificate}" -noout -checkhost "${value}" 2>&1)" \
                    || [[ "${validation_output}" != *"does match certificate"* ]]; then
                    echo "ERROR: TLS certificate SAN is missing required DNS identity: ${value}" >&2
                    return 1
                fi
                ;;
            *)
                echo "ERROR: certificate identities must use IP:value or DNS:value." >&2
                return 1
                ;;
        esac
    done
    echo "PCS Stats API TLS certificate, key, and required identities are valid."
}

write_runtime_env() {
    local temp
    temp="$(mktemp)"
    cat >"${temp}" <<EOF
PCS_API_ENABLED=yes
PCS_API_HOST=0.0.0.0
PCS_API_PORT=${PCS_API_PORT}
PCS_API_TOKEN_FILE=${PCS_API_TOKEN_FILE}
PCS_WEB_ACTION=${PCS_API_DISPATCHER}
PCS_API_CERT_FILE=${PCS_API_CERT_FILE}
PCS_API_KEY_FILE=${PCS_API_KEY_FILE}
PCS_API_TOKEN_HELPER=${TOKEN_HELPER_DST}
PCS_ADMIN_PASSWORD_HELPER=${PASSWORD_HELPER_DST}
PCS_BACKUP_CONFIG_HELPER=${BACKUP_CONFIG_HELPER_DST}
EOF
    sudo install -o root -g root -m 0644 "${temp}" "${RUNTIME_ENV}"
    rm -f -- "${temp}"
}

prepared() {
    sudo test -f "${APPLICATION_DST}" \
        && sudo test -x "${TOKEN_HELPER_DST}" \
        && sudo test -f "${PASSWORD_HELPER_DST}" \
        && sudo test -x "${PASSWORD_HELPER_DST}" \
        && sudo test ! -L "${PASSWORD_HELPER_DST}" \
        && sudo test -x "${BACKUP_CONFIG_HELPER_DST}" \
        && sudo test ! -L "${BACKUP_CONFIG_HELPER_DST}" \
        && sudo test -x "${FIREWALL_DST}" \
        && sudo test -x "${SETUP_DST}" \
        && sudo test -f "${SERVICE_DST}" \
        && sudo test -f "${FIREWALL_SERVICE_DST}"
}

prepare_feature() {
    require_normal_user
    require_sudo
    require_prepared_sources
    if ! sudo test -f "${PASSWORD_HELPER_DST}" \
        || ! sudo test -x "${PASSWORD_HELPER_DST}" \
        || sudo test -L "${PASSWORD_HELPER_DST}"; then
        echo "ERROR: install the PCS control-panel password helper first: ${PASSWORD_HELPER_DST}" >&2
        exit 1
    fi
    [[ "$(sudo stat -c '%U:%G %a' "${PASSWORD_HELPER_DST}")" == "root:root 755" ]] || {
        echo "ERROR: PCS control-panel password helper must be root:root mode 0755." >&2
        exit 1
    }
    confirm "Install the inactive PCS Stats API runtime components?" PCS_API_PREPARE_CONFIRM

    if systemctl is-active --quiet pcs-stats-api.service 2>/dev/null; then
        echo "ERROR: deactivate the Stats API before replacing installed components." >&2
        exit 1
    fi
    command -v openssl >/dev/null
    command -v nft >/dev/null
    command -v visudo >/dev/null

    if ! getent group "${API_GROUP}" >/dev/null; then
        sudo groupadd --system "${API_GROUP}"
    fi
    if ! id -u "${API_USER}" >/dev/null 2>&1; then
        sudo useradd --system --gid "${API_GROUP}" --home-dir /nonexistent --shell /usr/sbin/nologin "${API_USER}"
    fi
    sudo install -d -o root -g root -m 0755 "${APPLICATION_DIR}"
    # The policy and runtime environment are non-secret and must remain
    # readable to the normal operator for validation. Sensitive children keep
    # their own group-restricted directory and file modes.
    sudo install -d -o root -g root -m 0755 "${CONFIG_DIR}"
    sudo install -d -o root -g "${API_GROUP}" -m 0750 "${TLS_DIR}"
    sudo install -o root -g root -m 0755 "${APPLICATION_SRC}" "${APPLICATION_DST}"
    sudo install -o root -g root -m 0755 "${BASH_SOURCE[0]}" "${SETUP_DST}"
    sudo install -o root -g root -m 0755 "${TOKEN_HELPER_SRC}" "${TOKEN_HELPER_DST}"
    sudo install -o root -g root -m 0755 "${BACKUP_CONFIG_HELPER_SRC}" "${BACKUP_CONFIG_HELPER_DST}"
    sudo install -o root -g root -m 0755 "${FIREWALL_SRC}" "${FIREWALL_DST}"
    sudo install -o root -g root -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"
    sudo install -o root -g root -m 0644 "${FIREWALL_SERVICE_SRC}" "${FIREWALL_SERVICE_DST}"

    SUDOERS_TEMP="$(mktemp)"
    cat >"${SUDOERS_TEMP}" <<EOF
# PCS Stats API may invoke fixed collectors, fixed pairing, and only the web panel's exact administrative actions.
${API_USER} ALL=(root) NOPASSWD: /usr/local/sbin/pcs-web-action dashboard-public-json, /usr/local/sbin/pcs-web-action dashboard-json
${API_USER} ALL=(root) NOPASSWD: /usr/local/sbin/pcs-api-token --file /etc/pcs-stats-api/api-read-tokens.json pair-from-stdin
${API_USER} ALL=(root) NOPASSWD: /usr/local/sbin/pcs-admin-password-helper --change-from-stdin
${API_USER} ALL=(root) NOPASSWD: ${BACKUP_CONFIG_HELPER_DST} show, ${BACKUP_CONFIG_HELPER_DST} set-from-stdin
${API_USER} ALL=(root) NOPASSWD: /usr/local/sbin/pcs-web-action status, /usr/local/sbin/pcs-web-action self-test, /usr/local/sbin/pcs-web-action storage-status, /usr/local/sbin/pcs-web-action wifi-status, /usr/local/sbin/pcs-web-action wifi-connect, /usr/local/sbin/pcs-web-action wifi-disconnect, /usr/local/sbin/pcs-web-action cellular-status, /usr/local/sbin/pcs-web-action cellular-connect, /usr/local/sbin/pcs-web-action cellular-disconnect, /usr/local/sbin/pcs-web-action cellular-test, /usr/local/sbin/pcs-web-action meshtastic-status, /usr/local/sbin/pcs-web-action restart-meshtastic, /usr/local/sbin/pcs-web-action sync-backup, /usr/local/sbin/pcs-web-action mount-usb, /usr/local/sbin/pcs-web-action mount-new-usb, /usr/local/sbin/pcs-web-action safe-unmount-usb, /usr/local/sbin/pcs-web-action restart-services, /usr/local/sbin/pcs-web-action restart-samba, /usr/local/sbin/pcs-web-action restart-modemmanager, /usr/local/sbin/pcs-web-action sync-time, /usr/local/sbin/pcs-web-action restart-chrony, /usr/local/sbin/pcs-web-action restart-gpsd, /usr/local/sbin/pcs-web-action restart-logs, /usr/local/sbin/pcs-web-action reboot-system, /usr/local/sbin/pcs-web-action shutdown-system
EOF
    chmod 0600 "${SUDOERS_TEMP}"
    sudo visudo -cf "${SUDOERS_TEMP}"
    sudo install -o root -g root -m 0440 "${SUDOERS_TEMP}" "${SUDOERS_FILE}"
    rm -f -- "${SUDOERS_TEMP}"
    SUDOERS_TEMP=""
    sudo systemctl daemon-reload
    sudo systemctl disable --now pcs-stats-api.service pcs-stats-api-firewall.service >/dev/null 2>&1 || true
    echo "PCS Stats API components are prepared but disabled and inactive."
}

import_policy() {
    local source="${2:-}"
    require_normal_user
    require_sudo
    [[ -n "${source}" ]] || { echo "Usage: $0 --import-policy FILE" >&2; exit 2; }
    prepared || { echo "ERROR: run --prepare first." >&2; exit 1; }
    load_policy "${source}"
    confirm "Import this deployment-local Stats API policy?" PCS_API_IMPORT_POLICY_CONFIRM
    sudo install -o root -g root -m 0644 "${source}" "${POLICY_FILE}"
    load_policy "${POLICY_FILE}"
    write_runtime_env
    echo "PCS Stats API policy imported; services remain disabled and inactive."
}

import_tls() {
    local certificate="${2:-}"
    local key="${3:-}"
    require_normal_user
    require_sudo
    [[ -n "${certificate}" && -n "${key}" ]] || { echo "Usage: $0 --import-tls CERT KEY" >&2; exit 2; }
    prepared || { echo "ERROR: run --prepare first." >&2; exit 1; }
    [[ -f "${POLICY_FILE}" ]] || { echo "ERROR: import the policy first." >&2; exit 1; }
    load_policy "${POLICY_FILE}"
    validate_tls_pair "${certificate}" "${key}"
    confirm "Import this TLS certificate and private key?" PCS_API_IMPORT_TLS_CONFIRM
    sudo install -o root -g "${API_GROUP}" -m 0644 "${certificate}" "${CERT_FILE}"
    sudo install -o root -g "${API_GROUP}" -m 0640 "${key}" "${KEY_FILE}"
    echo "PCS Stats API TLS material imported; services remain disabled and inactive."
}

issue_token() {
    local token_id="${2:-}"
    require_normal_user
    require_sudo
    [[ -n "${token_id}" ]] || { echo "Usage: $0 --issue-token TOKEN_ID" >&2; exit 2; }
    prepared || { echo "ERROR: run --prepare first." >&2; exit 1; }
    sudo "${TOKEN_HELPER_DST}" --file "${TOKEN_FILE}" issue "${token_id}"
    sudo chown root:"${API_GROUP}" "${TOKEN_FILE}"
    sudo chmod 0640 "${TOKEN_FILE}"
}

revoke_token() {
    local token_id="${2:-}"
    require_normal_user
    require_sudo
    [[ -n "${token_id}" ]] || { echo "Usage: $0 --revoke-token TOKEN_ID" >&2; exit 2; }
    sudo "${TOKEN_HELPER_DST}" --file "${TOKEN_FILE}" revoke "${token_id}"
    sudo chown root:"${API_GROUP}" "${TOKEN_FILE}"
    sudo chmod 0640 "${TOKEN_FILE}"
}

deactivate_feature() {
    sudo systemctl disable --now pcs-stats-api.service >/dev/null 2>&1 || true
    sudo systemctl disable --now pcs-stats-api-firewall.service >/dev/null 2>&1 || true
    sudo "${FIREWALL_DST}" --clear >/dev/null 2>&1 || true
}

check_feature() {
    require_normal_user
    require_sudo
    prepared || { echo "ERROR: Stats API runtime is not prepared." >&2; exit 1; }
    load_policy "${POLICY_FILE}"
    validate_tls_pair "${CERT_FILE}" "${KEY_FILE}" yes
    sudo test -s "${TOKEN_FILE}" || { echo "ERROR: no API token store is configured." >&2; exit 1; }
    [[ "$(sudo stat -c '%U:%G %a' "${TOKEN_FILE}")" == "root:${API_GROUP} 640" ]]
    sudo visudo -cf "${SUDOERS_FILE}" >/dev/null
    [[ "$(sudo stat -c '%U:%G %a' "${PASSWORD_HELPER_DST}")" == "root:root 755" ]] \
        || { echo "ERROR: password helper ownership or mode is unsafe." >&2; exit 1; }
    sudo grep -Fxq "${API_USER} ALL=(root) NOPASSWD: /usr/local/sbin/pcs-api-token --file /etc/pcs-stats-api/api-read-tokens.json pair-from-stdin" "${SUDOERS_FILE}"
    sudo grep -Fxq "${API_USER} ALL=(root) NOPASSWD: /usr/local/sbin/pcs-admin-password-helper --change-from-stdin" "${SUDOERS_FILE}"
    local action
    for action in \
        status self-test storage-status restart-logs \
        wifi-status wifi-connect wifi-disconnect \
        cellular-status cellular-connect cellular-disconnect cellular-test \
        meshtastic-status restart-meshtastic \
        sync-backup mount-usb mount-new-usb safe-unmount-usb \
        restart-services restart-samba restart-modemmanager \
        sync-time restart-chrony restart-gpsd \
        reboot-system shutdown-system; do
        sudo grep -Fq "/usr/local/sbin/pcs-web-action ${action}" "${SUDOERS_FILE}" \
            || { echo "ERROR: sudoers is missing fixed action: ${action}" >&2; exit 1; }
    done
    sudo "${TOKEN_HELPER_DST}" --file "${TOKEN_FILE}" list \
        | awk '$2 == "enabled" { found=1 } END { exit !found }'
    systemctl is-enabled --quiet pcs-stats-api-firewall.service
    systemctl is-active --quiet pcs-stats-api-firewall.service
    systemctl is-enabled --quiet pcs-stats-api.service
    systemctl is-active --quiet pcs-stats-api.service
    sudo "${FIREWALL_DST}" --check
    curl --insecure --fail --silent --show-error --max-time 10 \
        "https://127.0.0.1:${PCS_API_PORT}/api/v1/status" \
        | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["access"] == "public"; assert data["details"] is None; assert "coordinates" not in json.dumps(data).lower()'
    echo "PCS Stats API checks passed."
}

activate_feature() {
    require_normal_user
    require_sudo
    confirm "Enable and start the HTTPS Stats API and its source firewall?" PCS_API_ACTIVATE_CONFIRM
    load_policy "${POLICY_FILE}"
    validate_tls_pair "${CERT_FILE}" "${KEY_FILE}" yes
    sudo test -s "${TOKEN_FILE}" || { echo "ERROR: issue at least one read-only token first." >&2; exit 1; }
    sudo "${TOKEN_HELPER_DST}" --file "${TOKEN_FILE}" list \
        | awk '$2 == "enabled" { found=1 } END { exit !found }' \
        || { echo "ERROR: issue at least one enabled read-only token first." >&2; exit 1; }
    write_runtime_env
    sudo systemctl start pcs-stats-api-firewall.service
    if ! sudo systemctl start pcs-stats-api.service; then
        deactivate_feature
        echo "ERROR: Stats API failed to start; services and firewall were deactivated." >&2
        exit 1
    fi
    sudo systemctl enable pcs-stats-api-firewall.service pcs-stats-api.service >/dev/null
    if ! check_feature; then
        deactivate_feature
        echo "ERROR: Stats API validation failed; services and firewall were deactivated." >&2
        exit 1
    fi
}

rollback_feature() {
    require_normal_user
    require_sudo
    confirm "Deactivate and remove Stats API programs and units while preserving policy, TLS, and token data?" PCS_API_ROLLBACK_CONFIRM
    deactivate_feature
    sudo rm -f -- "${SERVICE_DST}" "${FIREWALL_SERVICE_DST}" "${SUDOERS_FILE}" \
        "${TOKEN_HELPER_DST}" "${FIREWALL_DST}" "${APPLICATION_DST}" "${SETUP_DST}"
    sudo systemctl daemon-reload
    echo "PCS Stats API runtime removed. Deployment-local policy, TLS material, and hashed token data were preserved in ${CONFIG_DIR}."
}

usage() {
    cat <<EOF
Usage: $0 COMMAND [ARGUMENTS]

  --prepare                  Install disabled/inactive runtime components
  --validate-policy FILE     Validate a deployment policy without installing it
  --import-policy FILE       Import a validated deployment-local policy
  --validate-tls CERT KEY [POLICY]
                             Validate matching TLS material and required SANs
  --import-tls CERT KEY      Import TLS material with restricted permissions
  --issue-token TOKEN_ID     Issue one read-only bearer token and print it once
  --revoke-token TOKEN_ID    Revoke one token by id
  --activate                 Firewall-first enable/start and validate
  --check                    Validate the active API, firewall, TLS, and public view
  --deactivate               Disable and stop API/firewall; preserve all data
  --rollback                 Remove runtime files; preserve policy/TLS/tokens
  --help                     Show this help
EOF
}

case "${MODE}" in
    --prepare) prepare_feature ;;
    --validate-policy)
        load_policy "${2:-}"
        echo "PCS Stats API policy is valid."
        ;;
    --import-policy) import_policy "$@" ;;
    --validate-tls)
        load_policy "${4:-${POLICY_FILE}}"
        validate_tls_pair "${2:-}" "${3:-}"
        ;;
    --import-tls) import_tls "$@" ;;
    --issue-token) issue_token "$@" ;;
    --revoke-token) revoke_token "$@" ;;
    --activate) activate_feature ;;
    --check) check_feature ;;
    --deactivate)
        require_normal_user
        require_sudo
        deactivate_feature
        echo "PCS Stats API is disabled and inactive."
        ;;
    --rollback) rollback_feature ;;
    -h|--help) usage ;;
    *) usage >&2; exit 2 ;;
esac
