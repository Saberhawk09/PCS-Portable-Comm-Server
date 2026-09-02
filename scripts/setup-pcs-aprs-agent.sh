#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_CONFIG="${PCS_INSTALL_CONFIG:-${REPO_DIR}/config/pcs-install.conf}"
AGENT_SRC="${REPO_DIR}/scripts/pcs_aprs_agent.py"
SERVICE_SRC="${REPO_DIR}/systemd/pcs-aprs-agent.service"
DOC_SRC="${REPO_DIR}/docs/aprs-agent.md"
AGENT_DST="/usr/local/sbin/pcs-aprs-agent"
SERVICE_DST="/etc/systemd/system/pcs-aprs-agent.service"
CONFIG_DST="/etc/pcs/aprs-agent.conf"
DOC_DST="/usr/local/share/doc/pcs/aprs-agent.md"
DIREWOLF_CONFIG="/etc/direwolf.conf"
TEMP_DIR=""

cleanup() {
    if [[ -n "${TEMP_DIR}" && "${TEMP_DIR}" == /tmp/* && -d "${TEMP_DIR}" ]]; then
        rm -rf -- "${TEMP_DIR}"
    fi
}

trap cleanup EXIT

if [[ -f "${INSTALL_CONFIG}" ]]; then
    # shellcheck source=/dev/null
    source "${INSTALL_CONFIG}"
fi

PCS_APRS_AGENT_ENABLED="${PCS_APRS_AGENT_ENABLED:-no}"
PCS_APRS_ENGINE="${PCS_APRS_ENGINE:-direwolf}"
PCS_APRS_CALLSIGN="${PCS_APRS_CALLSIGN:-W8IJC-10}"
PCS_APRS_KISS_PORT="${PCS_APRS_KISS_PORT:-8001}"
PCS_APRS_AGENT_ICHANNEL="${PCS_APRS_AGENT_ICHANNEL:-8}"
PCS_APRS_AGENT_TOCALL="${PCS_APRS_AGENT_TOCALL:-APZPCS}"
PCS_APRS_AGENT_DEDUPE_TTL_SECONDS="${PCS_APRS_AGENT_DEDUPE_TTL_SECONDS:-86400}"
PCS_APRS_AGENT_SENDER_RATE_PER_MINUTE="${PCS_APRS_AGENT_SENDER_RATE_PER_MINUTE:-12}"
PCS_APRS_AGENT_GLOBAL_RATE_PER_MINUTE="${PCS_APRS_AGENT_GLOBAL_RATE_PER_MINUTE:-60}"
PCS_APRS_GPSD_PORT="${PCS_APRS_GPSD_PORT:-2947}"

usage() {
    cat <<'EOF'
Usage: ./scripts/setup-pcs-aprs-agent.sh COMMAND

  --install  Install and start the agent only after the live Dire Wolf file has
             the matching KISSPORT and Internet-only ICHANNEL.
  --check    Read-only validation of source, installed files, configuration,
             Dire Wolf channel mapping, and service state.

This script never writes /etc/direwolf.conf, handles APRS-IS credentials, or
controls audio, PTT, the radio, beaconing, digipeating, or IGate policy.
EOF
}

render_config() {
    cat <<EOF
[agent]
callsign = ${PCS_APRS_CALLSIGN}
tocall = ${PCS_APRS_AGENT_TOCALL}
kiss_host = 127.0.0.1
kiss_port = ${PCS_APRS_KISS_PORT}
kiss_channel = ${PCS_APRS_AGENT_ICHANNEL}
state_db = /var/lib/pcs-aprs-agent/state.sqlite3
dedupe_ttl_seconds = ${PCS_APRS_AGENT_DEDUPE_TTL_SECONDS}
gpsd_host = 127.0.0.1
gpsd_port = ${PCS_APRS_GPSD_PORT}
command_timeout_seconds = 3
sender_rate_per_minute = ${PCS_APRS_AGENT_SENDER_RATE_PER_MINUTE}
global_rate_per_minute = ${PCS_APRS_AGENT_GLOBAL_RATE_PER_MINUTE}
reconnect_max_seconds = 60
EOF
}

validate_live_mapping() {
    if [[ "${PCS_APRS_AGENT_ENABLED}" != "yes" ]]; then
        echo "ERROR: PCS_APRS_AGENT_ENABLED is not yes." >&2
        return 1
    fi
    if [[ "${PCS_APRS_ENGINE}" != "direwolf" ]]; then
        echo "ERROR: the APRS agent requires PCS_APRS_ENGINE=direwolf; ${PCS_APRS_ENGINE} is selected." >&2
        return 1
    fi
    if ! systemctl is-active --quiet direwolf.service; then
        echo "ERROR: direwolf.service must be active before the APRS agent is installed or started." >&2
        return 1
    fi
    if ! sudo test -s "${DIREWOLF_CONFIG}"; then
        echo "ERROR: no managed live Dire Wolf configuration is installed." >&2
        return 1
    fi
    if ! sudo grep -Eq "^KISSPORT[[:space:]]+${PCS_APRS_KISS_PORT}$" "${DIREWOLF_CONFIG}"; then
        echo "ERROR: live Dire Wolf KISSPORT does not match ${PCS_APRS_KISS_PORT}." >&2
        return 1
    fi
    if ! sudo grep -Eq "^ICHANNEL[[:space:]]+${PCS_APRS_AGENT_ICHANNEL}$" "${DIREWOLF_CONFIG}"; then
        echo "ERROR: live Dire Wolf does not map ICHANNEL ${PCS_APRS_AGENT_ICHANNEL}." >&2
        return 1
    fi
}

install_agent() {
    local config_file
    [[ "${EUID}" -ne 0 ]] || { echo "ERROR: run as the normal PCS user, not root." >&2; return 1; }
    for source in "${AGENT_SRC}" "${SERVICE_SRC}" "${DOC_SRC}"; do
        [[ -f "${source}" ]] || { echo "ERROR: missing repository file: ${source}" >&2; return 1; }
    done
    sudo -v
    validate_live_mapping
    TEMP_DIR="$(mktemp -d)"
    config_file="${TEMP_DIR}/aprs-agent.conf"
    render_config >"${config_file}"
    python3 "${AGENT_SRC}" --config "${config_file}" --check-config
    sudo install -d -o root -g root -m 0755 /etc/pcs /usr/local/share/doc/pcs
    sudo install -o root -g root -m 0755 "${AGENT_SRC}" "${AGENT_DST}"
    sudo install -o root -g root -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"
    sudo install -o root -g root -m 0644 "${DOC_SRC}" "${DOC_DST}"
    sudo install -o root -g root -m 0644 "${config_file}" "${CONFIG_DST}"
    sudo systemctl daemon-reload
    sudo systemctl enable --now pcs-aprs-agent.service
    sudo systemctl is-active --quiet pcs-aprs-agent.service
    echo "PCS APRS Agent installed and active; Dire Wolf remains authoritative."
}

check_agent() {
    local failed=0
    for source in "${AGENT_SRC}" "${SERVICE_SRC}" "${DOC_SRC}"; do
        [[ -f "${source}" ]] || { echo "FAIL: missing ${source}"; failed=1; }
    done
    if sudo test -s "${DIREWOLF_CONFIG}"; then
        validate_live_mapping || failed=1
    else
        echo "INFO: no live Dire Wolf configuration; local source remains undeployed."
    fi
    if [[ -x "${AGENT_DST}" && -f "${CONFIG_DST}" ]]; then
        sudo "${AGENT_DST}" --config "${CONFIG_DST}" --check-config || failed=1
        systemctl is-enabled --quiet pcs-aprs-agent.service || failed=1
        systemctl is-active --quiet pcs-aprs-agent.service || failed=1
    else
        echo "INFO: PCS APRS Agent is not installed on this host."
    fi
    return "${failed}"
}

case "${1:---help}" in
    --install) install_agent ;;
    --check) check_agent ;;
    -h|--help) usage ;;
    *) usage >&2; exit 2 ;;
esac
