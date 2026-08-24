#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "${1:-}" != "--run" ]]; then
    cat <<'EOF'
Usage: sudo ./scripts/test-time-source-failover.sh --run

Temporarily exercises the PCS Chrony hierarchy and restores normal source
selection on every exit path. This does not stop GPSD or change networking.
EOF
    exit 2
fi

if [[ "${EUID}" -ne 0 ]]; then
    exec sudo -n "$0" "$@"
fi

for command in chronyc curl python3; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        echo "ERROR: Required command is missing: ${command}" >&2
        exit 1
    fi
done

dashboard_time_source() {
    curl -fsS http://127.0.0.1/api/public-status \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["time"]["source"])'
}

tracking_reference() {
    chronyc tracking | awk -F: '/^Reference ID/ { sub(/^[[:space:]]+/, "", $2); print $2 }'
}

show_state() {
    chronyc sources -v
    chronyc tracking
    echo "Dashboard source: $(dashboard_time_source)"
}

restore_normal_selection() {
    chronyc online >/dev/null 2>&1 || true
    chronyc selectopts GPS -noselect >/dev/null 2>&1 || true
    chronyc burst 4/4 >/dev/null 2>&1 || true
    chronyc reselect >/dev/null 2>&1 || true
}

wait_for_reference() {
    local pattern="$1"
    local attempts="$2"
    local current_reference

    for _attempt in $(seq 1 "${attempts}"); do
        current_reference="$(tracking_reference || true)"
        echo "Waiting for reference ${pattern}: ${current_reference:-unavailable}"
        if grep -qE "${pattern}" <<<"${current_reference}"; then
            return 0
        fi
        sleep 2
    done

    return 1
}

wait_for_dashboard_source() {
    local expected="$1"
    local attempts="$2"

    for _attempt in $(seq 1 "${attempts}"); do
        if [[ "$(dashboard_time_source 2>/dev/null || true)" == "${expected}" ]]; then
            return 0
        fi
        sleep 2
    done

    return 1
}

trap restore_normal_selection EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "=== PCS Time-Source Failover Test ==="
echo
echo "--- Initial preferred GPS state ---"

if ! wait_for_reference '\(GPS\)' 20; then
    show_state
    echo "ERROR: GPS was not selected before the failover test." >&2
    exit 1
fi

show_state

echo
echo "--- Internet NTP fallback with GPS temporarily excluded ---"
chronyc selectopts GPS +noselect
chronyc burst 4/4
chronyc reselect

for _attempt in $(seq 1 20); do
    if chronyc sources -n | awk '$1 == "^*" { found=1 } END { exit found ? 0 : 1 }'; then
        break
    fi
    sleep 2
done

if ! chronyc sources -n | awk '$1 == "^*" { found=1 } END { exit found ? 0 : 1 }'; then
    show_state
    echo "ERROR: Internet NTP was not selected after GPS exclusion." >&2
    exit 1
fi

if ! wait_for_dashboard_source "Internet NTP" 10; then
    show_state
    echo "ERROR: Dashboard did not report Internet NTP during fallback." >&2
    exit 1
fi

show_state

echo
echo "--- RTC-seeded local holdover with network sources offline ---"
chronyc offline
chronyc reset sources
chronyc reselect

if ! wait_for_reference '^7F7F0101' 10; then
    show_state
    echo "ERROR: Chrony local holdover did not activate." >&2
    exit 1
fi

/usr/local/sbin/pcs-rtc-seed --check

if ! wait_for_dashboard_source "RTC holdover" 10; then
    show_state
    echo "ERROR: Dashboard did not report RTC holdover." >&2
    exit 1
fi

show_state

echo
echo "--- Restoring preferred GPS state ---"
restore_normal_selection

if ! wait_for_reference '\(GPS\)' 30; then
    show_state
    echo "ERROR: GPS was not reselected after restoration." >&2
    exit 1
fi

show_state

echo
echo "PCS time-source failover test passed and normal GPS selection was restored."
