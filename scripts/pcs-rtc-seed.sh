#!/usr/bin/env bash

set -Eeuo pipefail

RTC_DEVICE="${PCS_RTC_DEVICE:-/dev/rtc0}"
MINIMUM_RTC_EPOCH=1704067200  # 2024-01-01T00:00:00Z
MAXIMUM_RTC_EPOCH=4102444800  # 2100-01-01T00:00:00Z
MODE="seed"

usage() {
    cat <<'EOF'
Usage: pcs-rtc-seed [--check]

Read and validate the PCS hardware RTC. The default mode seeds the system
clock from the RTC; --check is read-only.
EOF
}

case "${1:-}" in
    "")
        ;;
    --check)
        MODE="check"
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

for _attempt in $(seq 1 10); do
    [[ -e "${RTC_DEVICE}" ]] && break
    sleep 1
done

if [[ ! -e "${RTC_DEVICE}" ]]; then
    echo "WARNING: PCS hardware RTC is unavailable at ${RTC_DEVICE}; leaving the system clock unchanged." >&2
    [[ "${MODE}" == "check" ]] && exit 1
    exit 0
fi

if ! rtc_output="$(hwclock --rtc="${RTC_DEVICE}" --show --utc 2>&1)"; then
    echo "WARNING: PCS hardware RTC could not be read; leaving the system clock unchanged." >&2
    echo "${rtc_output}" >&2
    [[ "${MODE}" == "check" ]] && exit 1
    exit 0
fi

if ! rtc_epoch="$(date --date="${rtc_output}" +%s 2>/dev/null)"; then
    echo "WARNING: PCS hardware RTC returned an unparseable value; leaving the system clock unchanged." >&2
    [[ "${MODE}" == "check" ]] && exit 1
    exit 0
fi

if (( rtc_epoch < MINIMUM_RTC_EPOCH || rtc_epoch >= MAXIMUM_RTC_EPOCH )); then
    echo "WARNING: PCS hardware RTC is outside the accepted 2024-2099 range; leaving the system clock unchanged." >&2
    [[ "${MODE}" == "check" ]] && exit 1
    exit 0
fi

if [[ "${MODE}" == "check" ]]; then
    echo "PCS hardware RTC is readable and has a plausible UTC value: ${rtc_output}"
    exit 0
fi

echo "Seeding the system clock from ${RTC_DEVICE}: ${rtc_output}"
hwclock --rtc="${RTC_DEVICE}" --hctosys --utc
