#!/usr/bin/env bash

set -Eeuo pipefail

KISS_PORT="${PCS_APRS_KISS_PORT:-0}"
LAN_INTERFACE="${PCS_APRS_KISS_LAN_INTERFACE:-eth0}"
LAN_NETWORK="${PCS_APRS_KISS_LAN_NETWORK:-10.42.0.0/24}"
MODE="${1:---apply}"

if [[ ! "${KISS_PORT}" =~ ^[0-9]+$ ]] \
    || (( KISS_PORT < 0 || KISS_PORT > 65535 )); then
    echo "ERROR: invalid PCS APRS KISS port: ${KISS_PORT}" >&2
    exit 2
fi

if [[ ! "${LAN_INTERFACE}" =~ ^[A-Za-z0-9_.:-]+$ ]]; then
    echo "ERROR: invalid PCS APRS LAN interface: ${LAN_INTERFACE}" >&2
    exit 2
fi

if [[ ! "${LAN_NETWORK}" =~ ^[0-9.]+/[0-9]{1,2}$ ]]; then
    echo "ERROR: invalid PCS APRS LAN network: ${LAN_NETWORK}" >&2
    exit 2
fi

if ! command -v nft >/dev/null 2>&1; then
    echo "ERROR: nft is required for PCS APRS KISS isolation." >&2
    exit 1
fi

clear_rules() {
    nft delete table inet pcs_aprs 2>/dev/null || true
}

apply_rules() {
    clear_rules
    if (( KISS_PORT == 0 )); then
        echo "PCS APRS KISS listener is disabled; no firewall table installed."
        return 0
    fi

    nft -f - <<EOF
table inet pcs_aprs {
    chain input {
        type filter hook input priority -10; policy accept;
        tcp dport ${KISS_PORT} iifname "lo" accept
        tcp dport ${KISS_PORT} iifname "${LAN_INTERFACE}" ip saddr ${LAN_NETWORK} accept
        tcp dport ${KISS_PORT} drop
    }
}
EOF
    echo "PCS APRS KISS tcp/${KISS_PORT} is limited to ${LAN_NETWORK} on ${LAN_INTERFACE}."
}

case "${MODE}" in
    --apply)
        apply_rules
        ;;
    --clear)
        clear_rules
        ;;
    --check)
        if (( KISS_PORT == 0 )); then
            if nft list table inet pcs_aprs >/dev/null 2>&1; then
                echo "ERROR: PCS APRS firewall table exists while KISS is disabled." >&2
                exit 1
            fi
            echo "PCS APRS KISS is disabled and no firewall table is present."
        else
            rules="$(nft list table inet pcs_aprs)"
            grep -F 'iifname "lo"' <<<"${rules}" | grep -Fq "tcp dport ${KISS_PORT}"
            grep -F "iifname \"${LAN_INTERFACE}\"" <<<"${rules}" \
                | grep -F "ip saddr ${LAN_NETWORK}" \
                | grep -Fq "tcp dport ${KISS_PORT}"
            grep -F "tcp dport ${KISS_PORT}" <<<"${rules}" | grep -Fq "drop"
            echo "PCS APRS KISS firewall rule is present for tcp/${KISS_PORT}."
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
