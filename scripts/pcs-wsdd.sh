#!/usr/bin/env bash

set -Eeuo pipefail

WSDD2="/usr/sbin/wsdd2"
HOST_ALIAS="pcs-file-share"
NETBIOS_NAME="PCS-FILE-SHARE"
WORKGROUP="WORKGROUP"
if [[ ! -x "${WSDD2}" ]]; then
    echo "ERROR: wsdd2 is not installed." >&2
    exit 1
fi

# wsdd2 supports only one -i filter. Multiple instances compete for UDP 3702
# and can discard each other's packets. Run one responder and let the service's
# nftables guard expose TCP/UDP 3702 only through lo, eth0, and wlan0.
exec "${WSDD2}" -4 \
    -H "${HOST_ALIAS}" \
    -N "${NETBIOS_NAME}" \
    -G "${WORKGROUP}" \
    -b "vendor:PCS,model:Portable Comm Server"
