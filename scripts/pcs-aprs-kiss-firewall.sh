#!/usr/bin/env bash

set -Eeuo pipefail

CONFIG_FILE="${PCS_APRS_FIREWALL_CONFIG:-/etc/pcs/aprs/kiss-firewall.conf}"
if [[ -r "${CONFIG_FILE}" ]]; then
    # shellcheck source=/dev/null
    source "${CONFIG_FILE}"
fi

AGW_PORT="${PCS_APRS_AGW_PORT:-0}"
KISS_PORT="${PCS_APRS_KISS_PORT:-0}"
LAN_INTERFACE="${PCS_APRS_KISS_LAN_INTERFACE:-eth0}"
LAN_NETWORK="${PCS_APRS_KISS_LAN_NETWORK:-10.42.0.0/24}"
MODE="${1:---apply}"

for port_name in AGW_PORT KISS_PORT; do
    port_value="${!port_name}"
    if [[ ! "${port_value}" =~ ^[0-9]+$ ]] \
        || (( port_value < 0 || port_value > 65535 )); then
        echo "ERROR: invalid PCS APRS ${port_name%_PORT} port: ${port_value}" >&2
        exit 2
    fi
done

if [[ ! "${LAN_INTERFACE}" =~ ^[A-Za-z0-9_.:-]+$ ]]; then
    echo "ERROR: invalid PCS APRS LAN interface: ${LAN_INTERFACE}" >&2
    exit 2
fi

if [[ ! "${LAN_NETWORK}" =~ ^[0-9.]+/[0-9]{1,2}$ ]]; then
    echo "ERROR: invalid PCS APRS LAN network: ${LAN_NETWORK}" >&2
    exit 2
fi

if ! command -v nft >/dev/null 2>&1; then
    echo "ERROR: nft is required for PCS APRS AGW/KISS isolation." >&2
    exit 1
fi

clear_rules() {
    nft delete table inet pcs_aprs 2>/dev/null || true
}

apply_rules() {
    local port_expression=""

    clear_rules
    if (( AGW_PORT == 0 && KISS_PORT == 0 )); then
        echo "PCS APRS AGW and KISS listeners are disabled; no firewall table installed."
        return 0
    fi

    if (( AGW_PORT > 0 && KISS_PORT > 0 && AGW_PORT != KISS_PORT )); then
        port_expression="{ ${AGW_PORT}, ${KISS_PORT} }"
    elif (( AGW_PORT > 0 )); then
        port_expression="${AGW_PORT}"
    else
        port_expression="${KISS_PORT}"
    fi

    nft -f - <<EOF
table inet pcs_aprs {
    chain input {
        type filter hook input priority -10; policy accept;
        tcp dport ${port_expression} iifname "lo" accept
        tcp dport ${port_expression} iifname "${LAN_INTERFACE}" ip saddr ${LAN_NETWORK} accept
        tcp dport ${port_expression} drop
    }
}
EOF
    echo "PCS APRS AGW tcp/${AGW_PORT} and KISS tcp/${KISS_PORT} are limited to ${LAN_NETWORK} on ${LAN_INTERFACE}."
}

check_port_rules() {
    local rules="$1"
    local label="$2"
    local port="$3"

    (( port > 0 )) || return 0
    grep -F 'iifname "lo"' <<<"${rules}" | grep -Fq "tcp dport" || return 1
    grep -F "iifname \"${LAN_INTERFACE}\"" <<<"${rules}" \
        | grep -F "ip saddr ${LAN_NETWORK}" \
        | grep -Fq "tcp dport" || return 1
    grep -F "tcp dport" <<<"${rules}" | grep -Fq "drop" || return 1
    grep -Eq "(^|[^0-9])${port}([^0-9]|$)" <<<"${rules}" || return 1
    echo "PCS APRS ${label} firewall rule is present for tcp/${port}."
}

case "${MODE}" in
    --apply)
        apply_rules
        ;;
    --clear)
        clear_rules
        ;;
    --check)
        if (( AGW_PORT == 0 && KISS_PORT == 0 )); then
            if nft list table inet pcs_aprs >/dev/null 2>&1; then
                echo "ERROR: PCS APRS firewall table exists while AGW and KISS are disabled." >&2
                exit 1
            fi
            echo "PCS APRS AGW and KISS are disabled and no firewall table is present."
        else
            rules="$(nft list table inet pcs_aprs)"
            check_port_rules "${rules}" AGW "${AGW_PORT}"
            check_port_rules "${rules}" KISS "${KISS_PORT}"
        fi
        ;;
    -h|--help)
        echo "Usage: pcs-aprs-kiss-firewall [--apply|--clear|--check]"
        ;;
    *)
        echo "Usage: pcs-aprs-kiss-firewall [--apply|--clear|--check]" >&2
        exit 2
        ;;
esac
