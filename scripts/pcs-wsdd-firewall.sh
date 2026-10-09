#!/usr/bin/env bash

# The topology marker is written only by the supervised VLAN migration.
PCS_NETWORK_MODE=legacy
if [[ -e /etc/pcs/network-mode ]]; then
    IFS= read -r PCS_NETWORK_MODE </etc/pcs/network-mode || exit 2
fi
case "${PCS_NETWORK_MODE}" in
    legacy) PCS_LAN_INTERFACE=eth0; PCS_LAN_PROFILE=pcs-router-wan-share ;;
    vlan) PCS_LAN_INTERFACE=eth0.10; PCS_LAN_PROFILE=pcs-lan-vlan ;;
    *) echo "ERROR: invalid PCS network mode; refusing network operation" >&2; exit 2 ;;
esac

set -Eeuo pipefail

NFT="/usr/sbin/nft"
TABLE_FAMILY="inet"
TABLE_NAME="pcs_wsdd"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: pcs-wsdd-firewall must run as root." >&2
    exit 1
fi
if [[ ! -x "${NFT}" ]]; then
    echo "ERROR: nft is not installed." >&2
    exit 1
fi

remove_table() {
    if "${NFT}" list table "${TABLE_FAMILY}" "${TABLE_NAME}" >/dev/null 2>&1; then
        "${NFT}" delete table "${TABLE_FAMILY}" "${TABLE_NAME}"
    fi
}

apply_rules() {
    remove_table
    "${NFT}" -f - <<EOF
table inet pcs_wsdd {
    chain input {
        type filter hook input priority -14; policy accept;
        iifname { "lo", "${PCS_LAN_INTERFACE}", "wlan0" } udp dport 3702 accept comment "pcs-wsdd-lan-udp"
        iifname { "lo", "${PCS_LAN_INTERFACE}", "wlan0" } tcp dport 3702 accept comment "pcs-wsdd-lan-tcp"
        iifname { "lo", "${PCS_LAN_INTERFACE}", "wlan0" } udp dport 5355 accept comment "pcs-wsdd-lan-llmnr-udp"
        iifname { "lo", "${PCS_LAN_INTERFACE}", "wlan0" } tcp dport 5355 accept comment "pcs-wsdd-lan-llmnr-tcp"
        udp dport 3702 drop comment "pcs-wsdd-default-deny-udp"
        tcp dport 3702 drop comment "pcs-wsdd-default-deny-tcp"
        udp dport 5355 drop comment "pcs-wsdd-default-deny-llmnr-udp"
        tcp dport 5355 drop comment "pcs-wsdd-default-deny-llmnr-tcp"
    }

    chain output {
        type filter hook output priority -14; policy accept;
        oifname { "lo", "${PCS_LAN_INTERFACE}", "wlan0" } udp sport 3702 accept comment "pcs-wsdd-lan-replies"
        oifname { "lo", "${PCS_LAN_INTERFACE}", "wlan0" } udp sport 5355 accept comment "pcs-wsdd-lan-llmnr-replies"
        udp sport 3702 drop comment "pcs-wsdd-nonlan-output-deny"
        udp sport 5355 drop comment "pcs-wsdd-nonlan-llmnr-output-deny"
    }
}
EOF
}

check_rules() {
    local rules
    rules="$("${NFT}" list table "${TABLE_FAMILY}" "${TABLE_NAME}")"
    for marker in \
        pcs-wsdd-lan-udp \
        pcs-wsdd-lan-tcp \
        pcs-wsdd-lan-llmnr-udp \
        pcs-wsdd-lan-llmnr-tcp \
        pcs-wsdd-default-deny-udp \
        pcs-wsdd-default-deny-tcp \
        pcs-wsdd-default-deny-llmnr-udp \
        pcs-wsdd-default-deny-llmnr-tcp \
        pcs-wsdd-lan-replies \
        pcs-wsdd-lan-llmnr-replies \
        pcs-wsdd-nonlan-output-deny \
        pcs-wsdd-nonlan-llmnr-output-deny; do
        grep -Fq "${marker}" <<<"${rules}" || {
            echo "ERROR: missing PCS WSD firewall rule: ${marker}" >&2
            return 1
        }
    done
}

case "${1:-}" in
    apply)
        apply_rules
        check_rules
        ;;
    remove)
        remove_table
        ;;
    check)
        check_rules
        ;;
    *)
        echo "Usage: $0 {apply|remove|check}" >&2
        exit 2
        ;;
esac
