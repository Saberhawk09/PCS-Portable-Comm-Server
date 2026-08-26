#!/usr/bin/env bash
set -euo pipefail

# Explicit, temporary lifecycle for the MPI3501-style ILI9486/XPT2046 HAT.
# Nothing in this file is installed as a service or run automatically.

STATE_DIR="${PCS_XPT2046_STATE_DIR:-/var/lib/pcs-xpt2046-test}"
STATE_FILE="${STATE_DIR}/service-state-v1"
LOCK_FILE="/run/lock/pcs-xpt2046-test.lock"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TOUCH_TOOL="${SCRIPT_DIR}/xpt2046_touch_test.py"
SERVICES=(
    pcs-gpio-startup.service
    pcs-gpio-lcd.service
    pcs-gpio-leds.service
    pcs-gpio-stats.service
    pcs-gpio-fan.service
    pcs-gpio-shutdown.service
)
STOP_ORDER=(
    pcs-gpio-lcd.service
    pcs-gpio-leds.service
    pcs-gpio-stats.service
    pcs-gpio-startup.service
    pcs-gpio-fan.service
    pcs-gpio-shutdown.service
)
START_ORDER=(
    pcs-gpio-shutdown.service
    pcs-gpio-fan.service
    pcs-gpio-startup.service
    pcs-gpio-lcd.service
    pcs-gpio-leds.service
    pcs-gpio-stats.service
)

usage() {
    cat <<'EOF'
Usage:
  sudo bash scripts/test-xpt2046-display.sh status
  sudo bash scripts/test-xpt2046-display.sh prepare --apply --confirm-test-screen-disconnected
  sudo bash scripts/test-xpt2046-display.sh adopt-suspended-test --apply \
    --confirm-original-services-enabled-active
  sudo bash scripts/test-xpt2046-display.sh test [--seconds N] --apply \
    --confirm-pcs-displays-disconnected
  sudo bash scripts/test-xpt2046-display.sh touch-test [--seconds N] --apply \
    --confirm-pcs-displays-disconnected
  sudo bash scripts/test-xpt2046-display.sh restore --apply \
    --confirm-pcs-hardware-restored

prepare snapshots, stops, and disables only the six related PCS GPIO units.
test loads a non-persistent ILI9486 overlay, displays a pattern, then removes it.
touch-test adds a non-persistent ADS7846 input overlay and plots touch reports.
restore exactly reinstates the saved enabled/active states and verifies them.
With no action, the script only prints this help. It installs no boot or service hooks.

adopt-suspended-test is only for recovery when prepare was done manually. Its long
confirmation means all six units were verified enabled+active before that manual stop.
EOF
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

require_root() {
    [[ "${EUID}" -eq 0 ]] || die "run this action through sudo"
}

has_arg() {
    local wanted="$1"
    shift
    local item
    for item in "$@"; do
        [[ "${item}" == "${wanted}" ]] && return 0
    done
    return 1
}

unit_enabled_state() {
    systemctl is-enabled "$1" 2>/dev/null || true
}

unit_active_state() {
    systemctl is-active "$1" 2>/dev/null || true
}

print_status() {
    local unit
    echo "=== PCS XPT2046 display test status ==="
    if [[ -r "${STATE_FILE}" ]]; then
        echo "Saved service state: ${STATE_FILE}"
        sed 's/^/  /' "${STATE_FILE}"
    else
        echo "Saved service state: none"
    fi
    echo "Current service state:"
    for unit in "${SERVICES[@]}"; do
        printf '  %-33s enabled=%-9s active=%s\n' \
            "${unit}" "$(unit_enabled_state "${unit}")" "$(unit_active_state "${unit}")"
    done
    echo "Dynamic overlays:"
    dtoverlay -l 2>/dev/null | sed 's/^/  /' || echo "  unavailable"
}

write_state() {
    local mode="$1"
    local temporary="${STATE_FILE}.new.$$"
    umask 077
    mkdir -p -- "${STATE_DIR}"
    {
        echo "version=1"
        echo "mode=${mode}"
        local unit
        for unit in "${SERVICES[@]}"; do
            if [[ "${mode}" == "observed" ]]; then
                printf '%s|%s|%s\n' "${unit}" \
                    "$(unit_enabled_state "${unit}")" "$(unit_active_state "${unit}")"
            else
                printf '%s|enabled|active\n' "${unit}"
            fi
        done
    } >"${temporary}"
    chmod 0600 "${temporary}"
    mv -- "${temporary}" "${STATE_FILE}"
}

require_suspended_units() {
    local unit enabled active
    for unit in "${SERVICES[@]}"; do
        enabled="$(unit_enabled_state "${unit}")"
        active="$(unit_active_state "${unit}")"
        [[ "${enabled}" == "disabled" ]] || \
            die "${unit} is ${enabled}, expected disabled"
        [[ "${active}" == "inactive" ]] || \
            die "${unit} is ${active}, expected inactive"
    done
}

prepare_test() {
    shift
    require_root
    has_arg --apply "$@" || die "prepare requires --apply"
    has_arg --confirm-test-screen-disconnected "$@" || \
        die "prepare requires --confirm-test-screen-disconnected"
    [[ ! -e "${STATE_FILE}" ]] || die "saved state already exists; use status or restore"
    local unit
    for unit in "${SERVICES[@]}"; do
        systemctl cat "${unit}" >/dev/null 2>&1 || die "unit not installed: ${unit}"
    done
    write_state observed
    echo "Saved original service state before making changes."
    for unit in "${STOP_ORDER[@]}"; do
        systemctl stop "${unit}"
    done
    for unit in "${STOP_ORDER[@]}"; do
        systemctl disable "${unit}"
    done
    require_suspended_units
    echo "Overlapping PCS GPIO services are disabled and inactive."
    echo "Shut down and remove power before swapping the GPIO hardware."
}

adopt_suspended_test() {
    shift
    require_root
    has_arg --apply "$@" || die "adopt-suspended-test requires --apply"
    has_arg --confirm-original-services-enabled-active "$@" || \
        die "adopt-suspended-test requires --confirm-original-services-enabled-active"
    [[ ! -e "${STATE_FILE}" ]] || die "saved state already exists; adoption refused"
    require_suspended_units
    write_state adopted-enabled-active
    echo "Recorded all six original service states as enabled and active."
    echo "No service or hardware state was changed by adoption."
}

find_ili9486_framebuffer() {
    local entry name
    for entry in /sys/class/graphics/fb*; do
        [[ -r "${entry}/name" ]] || continue
        name="$(tr '[:upper:]' '[:lower:]' <"${entry}/name")"
        if [[ "${name}" == *ili9486* || "${name}" == *fbtft* ]]; then
            echo "/dev/${entry##*/}"
            return 0
        fi
    done
    return 1
}

find_ads7846_input() {
    local entry name
    for entry in /sys/class/input/event*; do
        [[ -r "${entry}/device/name" ]] || continue
        name="$(tr '[:upper:]' '[:lower:]' <"${entry}/device/name")"
        if [[ "${name}" == *ads7846* || "${name}" == *xpt2046* ]]; then
            echo "/dev/input/${entry##*/}"
            return 0
        fi
    done
    return 1
}

run_display_test() {
    shift
    require_root
    has_arg --apply "$@" || die "test requires --apply"
    has_arg --confirm-pcs-displays-disconnected "$@" || \
        die "test requires --confirm-pcs-displays-disconnected"
    [[ -r "${STATE_FILE}" ]] || die "no saved service state; run prepare first"
    require_suspended_units

    local seconds=60 item previous="" framebuffer="" loaded=false
    local -a args=("$@")
    local index
    for ((index=0; index<${#args[@]}; index++)); do
        item="${args[index]}"
        if [[ "${item}" == "--seconds" ]]; then
            ((index + 1 < ${#args[@]})) || die "--seconds needs a value"
            seconds="${args[index+1]}"
            index=$((index + 1))
        fi
    done
    [[ "${seconds}" =~ ^[1-9][0-9]*$ ]] || die "--seconds must be a positive integer"
    (( seconds <= 1800 )) || die "--seconds must be 1800 or less"
    [[ -x "$(command -v dtoverlay)" ]] || die "dtoverlay is unavailable"
    [[ -f "${TOUCH_TOOL}" ]] || die "missing helper: ${TOUCH_TOOL}"
    previous="$(dtoverlay -l 2>/dev/null || true)"
    [[ "${previous}" != *fbtft* ]] || die "an fbtft overlay is already active"

    cleanup_overlay() {
        if [[ "${loaded}" == true ]]; then
            echo "Removing temporary fbtft overlay..."
            if ! dtoverlay -r fbtft >/dev/null 2>&1; then
                echo "ERROR: temporary fbtft overlay removal failed" >&2
                return 1
            fi
            loaded=false
        fi
    }
    on_exit() {
        local result=$?
        if ! cleanup_overlay; then
            result=1
        fi
        trap - EXIT
        exit "${result}"
    }
    trap on_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # MPI3501/tft35a profile: LCD CE0, ILI9486, reset GPIO25, D/C GPIO24.
    dtoverlay fbtft spi0-0 ili9486 speed=16000000 width=480 height=320 \
        regwidth=16 buswidth=8 reset_pin=25 dc_pin=24 rotate=90 fps=30
    loaded=true
    for _ in {1..50}; do
        framebuffer="$(find_ili9486_framebuffer || true)"
        [[ -n "${framebuffer}" ]] && break
        sleep 0.1
    done
    [[ -n "${framebuffer}" ]] || die "ILI9486 framebuffer did not appear"
    python3 "${TOUCH_TOOL}" pattern --framebuffer "${framebuffer}" \
        --hardware --apply --confirm-pcs-displays-disconnected
    echo "Pattern is live on ${framebuffer} for ${seconds} seconds (Ctrl-C ends early)."
    sleep "${seconds}"
    echo "Display test interval completed."
}

run_touch_test() {
    shift
    require_root
    has_arg --apply "$@" || die "touch-test requires --apply"
    has_arg --confirm-pcs-displays-disconnected "$@" || \
        die "touch-test requires --confirm-pcs-displays-disconnected"
    [[ -r "${STATE_FILE}" ]] || die "no saved service state; run prepare first"
    require_suspended_units

    local seconds=60 item framebuffer="" input_device=""
    local display_loaded=false touch_loaded=false
    local -a args=("$@")
    local index
    for ((index=0; index<${#args[@]}; index++)); do
        item="${args[index]}"
        if [[ "${item}" == "--seconds" ]]; then
            ((index + 1 < ${#args[@]})) || die "--seconds needs a value"
            seconds="${args[index+1]}"
            index=$((index + 1))
        fi
    done
    [[ "${seconds}" =~ ^[1-9][0-9]*$ ]] || die "--seconds must be a positive integer"
    (( seconds <= 1800 )) || die "--seconds must be 1800 or less"
    [[ "$(dtoverlay -l 2>/dev/null || true)" != *fbtft* ]] || \
        die "an fbtft overlay is already active"
    [[ "$(dtoverlay -l 2>/dev/null || true)" != *ads7846* ]] || \
        die "an ads7846 overlay is already active"

    cleanup_touch_overlays() {
        local failed=false
        if [[ "${touch_loaded}" == true ]]; then
            echo "Removing temporary ads7846 overlay..."
            if dtoverlay -r ads7846 >/dev/null 2>&1; then
                touch_loaded=false
            else
                echo "ERROR: temporary ads7846 overlay removal failed" >&2
                failed=true
            fi
        fi
        if [[ "${display_loaded}" == true ]]; then
            echo "Removing temporary fbtft overlay..."
            if dtoverlay -r fbtft >/dev/null 2>&1; then
                display_loaded=false
            else
                echo "ERROR: temporary fbtft overlay removal failed" >&2
                failed=true
            fi
        fi
        [[ "${failed}" == false ]]
    }
    on_touch_exit() {
        local result=$?
        if ! cleanup_touch_overlays; then
            result=1
        fi
        trap - EXIT
        exit "${result}"
    }
    trap on_touch_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    dtoverlay fbtft spi0-0 ili9486 speed=16000000 width=480 height=320 \
        regwidth=16 buswidth=8 reset_pin=25 dc_pin=24 rotate=90 fps=30
    display_loaded=true
    dtoverlay ads7846 cs=1 speed=2000000 penirq=17 penirq_pull=2 \
        xohms=60 pmax=255
    touch_loaded=true
    for _ in {1..50}; do
        framebuffer="$(find_ili9486_framebuffer || true)"
        input_device="$(find_ads7846_input || true)"
        [[ -n "${framebuffer}" && -n "${input_device}" ]] && break
        sleep 0.1
    done
    [[ -n "${framebuffer}" ]] || die "ILI9486 framebuffer did not appear"
    [[ -n "${input_device}" ]] || die "ADS7846 input device did not appear"
    python3 "${TOUCH_TOOL}" touch-map --framebuffer "${framebuffer}" \
        --input-device "${input_device}" --seconds "${seconds}" \
        --hardware --apply --confirm-pcs-displays-disconnected
}

read_saved_state() {
    local unit="$1"
    awk -F'|' -v wanted="${unit}" '$1 == wanted { print $2 "|" $3 }' "${STATE_FILE}"
}

restore_services() {
    shift
    require_root
    has_arg --apply "$@" || die "restore requires --apply"
    has_arg --confirm-pcs-hardware-restored "$@" || \
        die "restore requires --confirm-pcs-hardware-restored"
    [[ -r "${STATE_FILE}" ]] || die "no saved service state to restore"
    [[ "$(dtoverlay -l 2>/dev/null || true)" != *fbtft* ]] || \
        die "temporary fbtft overlay is still active"

    local unit saved enabled active
    for unit in "${SERVICES[@]}"; do
        saved="$(read_saved_state "${unit}")"
        [[ -n "${saved}" ]] || die "saved state is missing ${unit}"
        enabled="${saved%%|*}"
        active="${saved#*|}"
        [[ "${enabled}" == "enabled" || "${enabled}" == "disabled" ]] || \
            die "unsupported saved enable state for ${unit}: ${enabled}"
        [[ "${active}" == "active" || "${active}" == "inactive" ]] || \
            die "unsupported saved active state for ${unit}: ${active}"
    done

    for unit in "${START_ORDER[@]}"; do
        saved="$(read_saved_state "${unit}")"
        enabled="${saved%%|*}"
        if [[ "${enabled}" == "enabled" ]]; then
            systemctl enable "${unit}"
        else
            systemctl disable "${unit}"
        fi
    done
    for unit in "${START_ORDER[@]}"; do
        saved="$(read_saved_state "${unit}")"
        active="${saved#*|}"
        if [[ "${active}" == "active" ]]; then
            systemctl start "${unit}"
        else
            systemctl stop "${unit}"
        fi
    done

    for unit in "${SERVICES[@]}"; do
        saved="$(read_saved_state "${unit}")"
        enabled="${saved%%|*}"
        active="${saved#*|}"
        [[ "$(unit_enabled_state "${unit}")" == "${enabled}" ]] || \
            die "${unit} enable state did not restore to ${enabled}"
        [[ "$(unit_active_state "${unit}")" == "${active}" ]] || \
            die "${unit} active state did not restore to ${active}"
    done
    rm -f -- "${STATE_FILE}"
    echo "Saved GPIO service states restored and verified."
    systemctl --failed --no-legend --plain || true
}

main() {
    local action="${1:-help}"
    if [[ "${action}" == "help" || "${action}" == "--help" || "${action}" == "-h" ]]; then
        usage
        return 0
    fi
    exec 9>"${LOCK_FILE}"
    flock -n 9 || die "another XPT2046 test lifecycle command is running"
    case "${action}" in
        status) print_status ;;
        prepare) prepare_test "$@" ;;
        adopt-suspended-test) adopt_suspended_test "$@" ;;
        test) run_display_test "$@" ;;
        touch-test) run_touch_test "$@" ;;
        restore) restore_services "$@" ;;
        *) usage; die "unknown action: ${action}" ;;
    esac
}

main "$@"
