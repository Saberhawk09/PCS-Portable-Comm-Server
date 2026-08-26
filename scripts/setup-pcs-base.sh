#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Do not run this script with sudo."
    echo "Run it as the normal Pi user. The individual setup steps will ask for sudo when needed."
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USB_UUID_DEFAULT="340B-4403"
INSTALL_CONFIG="${PCS_INSTALL_CONFIG:-${REPO_DIR}/config/pcs-install.conf}"

if [[ -f "${INSTALL_CONFIG}" ]]; then
    # shellcheck source=/dev/null
    source "${INSTALL_CONFIG}"
fi

PCS_CELLULAR_PROFILE_DEFAULT="pcs-cellular-profile"
PCS_CELLULAR_APN_DEFAULT="fast.t-mobile.com"
PCS_CELLULAR_ROUTE_METRIC_DEFAULT="900"
PCS_CELLULAR_FALLBACK_MODE_DEFAULT="manual"
PCS_SAMBA_WORKGROUP_DEFAULT="WORKGROUP"

PCS_SETUP_MODE="ASK"
PCS_CELLULAR_PROFILE="${PCS_CELLULAR_PROFILE:-${PCS_CELLULAR_PROFILE_DEFAULT}}"
PCS_CELLULAR_APN="${PCS_CELLULAR_APN:-${PCS_CELLULAR_APN_DEFAULT}}"
PCS_CELLULAR_ROUTE_METRIC="${PCS_CELLULAR_ROUTE_METRIC:-${PCS_CELLULAR_ROUTE_METRIC_DEFAULT}}"
PCS_CELLULAR_FALLBACK_MODE="${PCS_CELLULAR_FALLBACK_MODE:-${PCS_CELLULAR_FALLBACK_MODE_DEFAULT}}"
PCS_SAMBA_USER="${PCS_SAMBA_USER:-${USER}}"
PCS_SAMBA_WORKGROUP="${PCS_SAMBA_WORKGROUP:-${PCS_SAMBA_WORKGROUP_DEFAULT}}"
PCS_SAMBA_PASSWORD="${PCS_SAMBA_PASSWORD:-}"
PCS_SETUP_USB_PRIMARY="${PCS_SETUP_USB_PRIMARY:-ask}"
PCS_SETUP_USB_DEVICE="${PCS_SETUP_USB_DEVICE:-auto}"
PCS_SETUP_WWAN_GPS="${PCS_SETUP_WWAN_GPS:-ask}"
PCS_SETUP_GPSD_LAN="${PCS_SETUP_GPSD_LAN:-ask}"
PCS_SETUP_PISTAR="${PCS_SETUP_PISTAR:-ask}"
PCS_SETUP_APRS="${PCS_SETUP_APRS:-ask}"
PCS_SETUP_MESHTASTIC="${PCS_SETUP_MESHTASTIC:-ask}"
PCS_SETUP_GPIO_LCD="${PCS_SETUP_GPIO_LCD:-ask}"
PCS_SETUP_GPIO_LEDS="${PCS_SETUP_GPIO_LEDS:-ask}"
PCS_SETUP_GPIO_STATS="${PCS_SETUP_GPIO_STATS:-ask}"
PCS_SETUP_GPIO_FAN="${PCS_SETUP_GPIO_FAN:-ask}"
PCS_APRS_CONFIG_VERSION="${PCS_APRS_CONFIG_VERSION:-2}"
PCS_APRS_ACTIVE_MODE="${PCS_APRS_ACTIVE_MODE:-staged}"
PCS_APRS_ROLE="${PCS_APRS_ROLE:-digi-igate}"
PCS_APRS_CALLSIGN="${PCS_APRS_CALLSIGN:-W8IJC-10}"
PCS_APRS_FREQUENCY="${PCS_APRS_FREQUENCY:-144.550 MHz}"
PCS_APRS_RADIO="${PCS_APRS_RADIO:-SA818S / EasyDigi / Sabrent USB audio}"
PCS_APRS_AUDIO_INPUT="${PCS_APRS_AUDIO_INPUT:-plughw:CARD=Device,DEV=0}"
PCS_APRS_AUDIO_OUTPUT="${PCS_APRS_AUDIO_OUTPUT:-plughw:CARD=Device,DEV=0}"
PCS_APRS_AUDIO_CARD="${PCS_APRS_AUDIO_CARD:-Device}"
PCS_APRS_PLAYBACK_CONTROL="${PCS_APRS_PLAYBACK_CONTROL:-Speaker}"
PCS_APRS_PLAYBACK_LEVEL="${PCS_APRS_PLAYBACK_LEVEL:--16dB}"
PCS_APRS_CAPTURE_CONTROL="${PCS_APRS_CAPTURE_CONTROL:-Mic}"
PCS_APRS_CAPTURE_LEVEL="${PCS_APRS_CAPTURE_LEVEL:-69%}"
PCS_APRS_AGC_CONTROL="${PCS_APRS_AGC_CONTROL:-Auto Gain Control}"
PCS_APRS_AGC_STATE="${PCS_APRS_AGC_STATE:-off}"
PCS_APRS_SAMPLE_RATE="${PCS_APRS_SAMPLE_RATE:-48000}"
PCS_APRS_AUDIO_CHANNELS="${PCS_APRS_AUDIO_CHANNELS:-1}"
PCS_APRS_MODEM="${PCS_APRS_MODEM:-1200}"
PCS_APRS_PTT_METHOD="${PCS_APRS_PTT_METHOD:-gpio}"
PCS_APRS_PTT_INTERFACE="${PCS_APRS_PTT_INTERFACE:-EasyDigi}"
PCS_APRS_PTT_GPIO_LINE="${PCS_APRS_PTT_GPIO_LINE:-6}"
PCS_APRS_PTT_ACTIVE_LEVEL="${PCS_APRS_PTT_ACTIVE_LEVEL:-high}"
PCS_APRS_AGW_PORT="${PCS_APRS_AGW_PORT:-8000}"
PCS_APRS_KISS_PORT="${PCS_APRS_KISS_PORT:-8001}"
PCS_APRS_KISS_LAN_INTERFACE="${PCS_APRS_KISS_LAN_INTERFACE:-eth0}"
PCS_APRS_KISS_LAN_NETWORK="${PCS_APRS_KISS_LAN_NETWORK:-10.42.0.0/24}"
PCS_APRS_IGATE="${PCS_APRS_IGATE:-yes}"
PCS_APRS_IGATE_SERVER="${PCS_APRS_IGATE_SERVER:-noam.aprs2.net}"
PCS_APRS_IGATE_MODE="${PCS_APRS_IGATE_MODE:-two-way}"
PCS_APRS_IGATE_RF_TO_IS_FILTER="${PCS_APRS_IGATE_RF_TO_IS_FILTER:-all-eligible}"
PCS_APRS_IGATE_IS_TO_RF_FILTER="${PCS_APRS_IGATE_IS_TO_RF_FILTER:-normal-messages}"
PCS_APRS_IGATE_TX_PATH="${PCS_APRS_IGATE_TX_PATH:-direct}"
PCS_APRS_IGATE_TX_LIMIT_1M="${PCS_APRS_IGATE_TX_LIMIT_1M:-6}"
PCS_APRS_IGATE_TX_LIMIT_5M="${PCS_APRS_IGATE_TX_LIMIT_5M:-10}"
PCS_APRS_GPSD="${PCS_APRS_GPSD:-yes}"
PCS_APRS_GPSD_HOST="${PCS_APRS_GPSD_HOST:-localhost}"
PCS_APRS_GPSD_PORT="${PCS_APRS_GPSD_PORT:-2947}"
PCS_APRS_BEACON="${PCS_APRS_BEACON:-yes}"
PCS_APRS_BEACON_TYPE="${PCS_APRS_BEACON_TYPE:-gps-tracker}"
PCS_APRS_BEACON_INTERVAL="${PCS_APRS_BEACON_INTERVAL:-10:00}"
PCS_APRS_BEACON_PATH="${PCS_APRS_BEACON_PATH:-direct}"
PCS_APRS_BEACON_SENDTO="${PCS_APRS_BEACON_SENDTO:-BOTH}"
PCS_APRS_BEACON_SYMBOL="${PCS_APRS_BEACON_SYMBOL:-igate}"
PCS_APRS_BEACON_OVERLAY="${PCS_APRS_BEACON_OVERLAY:-T}"
PCS_APRS_BEACON_ALTITUDE="${PCS_APRS_BEACON_ALTITUDE:-yes}"
PCS_APRS_BEACON_COMMENT="${PCS_APRS_BEACON_COMMENT:-PCS Portable Communication Server - W8IJC}"
PCS_APRS_DIGIPEAT="${PCS_APRS_DIGIPEAT:-yes}"
PCS_APRS_DIGIPEAT_MODE="${PCS_APRS_DIGIPEAT_MODE:-fill-in}"
PCS_APRS_DIGIPEAT_ALIAS="${PCS_APRS_DIGIPEAT_ALIAS:-WIDE1-1}"
PCS_APRS_DIGIPEAT_ALIAS_PATTERN="${PCS_APRS_DIGIPEAT_ALIAS_PATTERN:-^WIDE1-1$}"
PCS_APRS_DIGIPEAT_WIDE_PATTERN="${PCS_APRS_DIGIPEAT_WIDE_PATTERN:-^WIDE1-1$}"
PCS_APRS_DIGIPEAT_PREEMPTIVE="${PCS_APRS_DIGIPEAT_PREEMPTIVE:-OFF}"
PCS_APRS_DIGIPEAT_FILTER="${PCS_APRS_DIGIPEAT_FILTER:-all-eligible}"
PCS_APRS_DIGIPEAT_DEDUPE_SECONDS="${PCS_APRS_DIGIPEAT_DEDUPE_SECONDS:-30}"
PCS_APRS_TX_ENABLED="${PCS_APRS_TX_ENABLED:-yes}"
PCS_APRS_FX25_TX="${PCS_APRS_FX25_TX:-no}"
PCS_APRS_LOGGING="${PCS_APRS_LOGGING:-yes}"
PCS_APRS_RADIO_INIT="${PCS_APRS_RADIO_INIT:-yes}"
PCS_APRS_RADIO_DEVICE="${PCS_APRS_RADIO_DEVICE:-/dev/serial0}"
PCS_APRS_RADIO_BAUD="${PCS_APRS_RADIO_BAUD:-9600}"
PCS_APRS_RADIO_BANDWIDTH_KHZ="${PCS_APRS_RADIO_BANDWIDTH_KHZ:-25}"
PCS_APRS_RADIO_TX_FREQUENCY_MHZ="${PCS_APRS_RADIO_TX_FREQUENCY_MHZ:-144.5500}"
PCS_APRS_RADIO_RX_FREQUENCY_MHZ="${PCS_APRS_RADIO_RX_FREQUENCY_MHZ:-144.5500}"
PCS_APRS_RADIO_TX_TONE="${PCS_APRS_RADIO_TX_TONE:-0000}"
PCS_APRS_RADIO_RX_TONE="${PCS_APRS_RADIO_RX_TONE:-0000}"
PCS_APRS_RADIO_SQUELCH="${PCS_APRS_RADIO_SQUELCH:-1}"
PCS_APRS_RADIO_VOLUME="${PCS_APRS_RADIO_VOLUME:-8}"
PCS_APRS_RADIO_PRE_DE_EMPHASIS="${PCS_APRS_RADIO_PRE_DE_EMPHASIS:-off}"
PCS_APRS_RADIO_HIGH_PASS="${PCS_APRS_RADIO_HIGH_PASS:-off}"
PCS_APRS_RADIO_LOW_PASS="${PCS_APRS_RADIO_LOW_PASS:-off}"
PCS_APRS_RADIO_TX_TAIL="${PCS_APRS_RADIO_TX_TAIL:-off}"
PCS_APRS_LOG_RETENTION_DAYS="${PCS_APRS_LOG_RETENTION_DAYS:-14}"
PCS_APRS_DWAIT="${PCS_APRS_DWAIT:-0}"
PCS_APRS_SLOTTIME="${PCS_APRS_SLOTTIME:-10}"
PCS_APRS_PERSIST="${PCS_APRS_PERSIST:-63}"
PCS_APRS_TXDELAY="${PCS_APRS_TXDELAY:-70}"
PCS_APRS_TXTAIL="${PCS_APRS_TXTAIL:-20}"
PCS_APRS_FULLDUP="${PCS_APRS_FULLDUP:-OFF}"
PCS_APRS_RX_AUDIO_VALIDATED="${PCS_APRS_RX_AUDIO_VALIDATED:-no}"
PCS_APRS_RADIO_CHANNEL_VALIDATED="${PCS_APRS_RADIO_CHANNEL_VALIDATED:-no}"
PCS_APRS_PTT_VALIDATED="${PCS_APRS_PTT_VALIDATED:-no}"
PCS_APRS_TX_AUDIO_VALIDATED="${PCS_APRS_TX_AUDIO_VALIDATED:-no}"
PCS_APRS_TX_TIMING_VALIDATED="${PCS_APRS_TX_TIMING_VALIDATED:-no}"

is_yes() {
    case "${1:-}" in
        y|Y|yes|YES|Yes)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

is_no() {
    case "${1:-}" in
        n|N|no|NO|No)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

ask_yes_no() {
    local prompt="$1"
    local default_answer="${2:-}"
    local answer

    while true; do
        if [[ -n "${default_answer}" ]]; then
            read -r -p "${prompt} [Y/N, default: ${default_answer}] " answer
            answer="${answer:-${default_answer}}"
        else
            read -r -p "${prompt} [Y/N] " answer
        fi

        if is_yes "${answer}"; then
            echo "yes"
            return 0
        fi

        if is_no "${answer}"; then
            echo "no"
            return 0
        fi

        echo "Please answer Y or N."
    done
}

ask_value() {
    local prompt="$1"
    local default_value="$2"
    local answer

    read -r -p "${prompt} [${default_value}] " answer
    echo "${answer:-${default_value}}"
}

ask_secret_confirm() {
    local prompt="$1"
    local output_var="$2"
    local first
    local second

    while true; do
        read -r -s -p "${prompt}: " first
        echo
        read -r -s -p "Confirm ${prompt}: " second
        echo

        if [[ -z "${first}" ]]; then
            echo "Password cannot be empty."
            continue
        fi

        if [[ "${first}" != "${second}" ]]; then
            echo "Passwords did not match."
            continue
        fi

        printf -v "${output_var}" '%s' "${first}"
        return 0
    done
}

write_install_config() {
    mkdir -p "$(dirname "${INSTALL_CONFIG}")"

    {
        echo "# PCS install config"
        echo "# Generated by scripts/setup-pcs-base.sh"
        printf "PCS_CELLULAR_PROFILE=%q\n" "${PCS_CELLULAR_PROFILE}"
        printf "PCS_CELLULAR_APN=%q\n" "${PCS_CELLULAR_APN}"
        printf "PCS_CELLULAR_ROUTE_METRIC=%q\n" "${PCS_CELLULAR_ROUTE_METRIC}"
        printf "PCS_CELLULAR_FALLBACK_MODE=%q\n" "${PCS_CELLULAR_FALLBACK_MODE}"
        printf "PCS_SAMBA_USER=%q\n" "${PCS_SAMBA_USER}"
        printf "PCS_SAMBA_WORKGROUP=%q\n" "${PCS_SAMBA_WORKGROUP}"
        echo "# PCS_SAMBA_PASSWORD is intentionally not written to disk."
        printf "PCS_SETUP_USB_PRIMARY=%q\n" "${PCS_SETUP_USB_PRIMARY}"
        printf "PCS_SETUP_USB_DEVICE=%q\n" "${PCS_SETUP_USB_DEVICE}"
        printf "PCS_SETUP_WWAN_GPS=%q\n" "${PCS_SETUP_WWAN_GPS}"
        printf "PCS_SETUP_GPSD_LAN=%q\n" "${PCS_SETUP_GPSD_LAN}"
        printf "PCS_SETUP_PISTAR=%q\n" "${PCS_SETUP_PISTAR}"
        printf "PCS_SETUP_APRS=%q\n" "${PCS_SETUP_APRS}"
        printf "PCS_SETUP_MESHTASTIC=%q\n" "${PCS_SETUP_MESHTASTIC}"
        printf "PCS_SETUP_GPIO_LCD=%q\n" "${PCS_SETUP_GPIO_LCD}"
        printf "PCS_SETUP_GPIO_LEDS=%q\n" "${PCS_SETUP_GPIO_LEDS}"
        printf "PCS_SETUP_GPIO_STATS=%q\n" "${PCS_SETUP_GPIO_STATS}"
        printf "PCS_SETUP_GPIO_FAN=%q\n" "${PCS_SETUP_GPIO_FAN}"
        printf "PCS_APRS_CONFIG_VERSION=%q\n" "${PCS_APRS_CONFIG_VERSION}"
        printf "PCS_APRS_ACTIVE_MODE=%q\n" "${PCS_APRS_ACTIVE_MODE}"
        printf "PCS_APRS_ROLE=%q\n" "${PCS_APRS_ROLE}"
        printf "PCS_APRS_CALLSIGN=%q\n" "${PCS_APRS_CALLSIGN}"
        printf "PCS_APRS_FREQUENCY=%q\n" "${PCS_APRS_FREQUENCY}"
        printf "PCS_APRS_RADIO=%q\n" "${PCS_APRS_RADIO}"
        printf "PCS_APRS_AUDIO_INPUT=%q\n" "${PCS_APRS_AUDIO_INPUT}"
        printf "PCS_APRS_AUDIO_OUTPUT=%q\n" "${PCS_APRS_AUDIO_OUTPUT}"
        printf "PCS_APRS_AUDIO_CARD=%q\n" "${PCS_APRS_AUDIO_CARD}"
        printf "PCS_APRS_PLAYBACK_CONTROL=%q\n" "${PCS_APRS_PLAYBACK_CONTROL}"
        printf "PCS_APRS_PLAYBACK_LEVEL=%q\n" "${PCS_APRS_PLAYBACK_LEVEL}"
        printf "PCS_APRS_CAPTURE_CONTROL=%q\n" "${PCS_APRS_CAPTURE_CONTROL}"
        printf "PCS_APRS_CAPTURE_LEVEL=%q\n" "${PCS_APRS_CAPTURE_LEVEL}"
        printf "PCS_APRS_AGC_CONTROL=%q\n" "${PCS_APRS_AGC_CONTROL}"
        printf "PCS_APRS_AGC_STATE=%q\n" "${PCS_APRS_AGC_STATE}"
        printf "PCS_APRS_SAMPLE_RATE=%q\n" "${PCS_APRS_SAMPLE_RATE}"
        printf "PCS_APRS_AUDIO_CHANNELS=%q\n" "${PCS_APRS_AUDIO_CHANNELS}"
        printf "PCS_APRS_MODEM=%q\n" "${PCS_APRS_MODEM}"
        printf "PCS_APRS_PTT_METHOD=%q\n" "${PCS_APRS_PTT_METHOD}"
        printf "PCS_APRS_PTT_INTERFACE=%q\n" "${PCS_APRS_PTT_INTERFACE}"
        printf "PCS_APRS_PTT_GPIO_LINE=%q\n" "${PCS_APRS_PTT_GPIO_LINE}"
        printf "PCS_APRS_PTT_ACTIVE_LEVEL=%q\n" "${PCS_APRS_PTT_ACTIVE_LEVEL}"
        printf "PCS_APRS_AGW_PORT=%q\n" "${PCS_APRS_AGW_PORT}"
        printf "PCS_APRS_KISS_PORT=%q\n" "${PCS_APRS_KISS_PORT}"
        printf "PCS_APRS_KISS_LAN_INTERFACE=%q\n" "${PCS_APRS_KISS_LAN_INTERFACE}"
        printf "PCS_APRS_KISS_LAN_NETWORK=%q\n" "${PCS_APRS_KISS_LAN_NETWORK}"
        printf "PCS_APRS_IGATE=%q\n" "${PCS_APRS_IGATE}"
        printf "PCS_APRS_IGATE_SERVER=%q\n" "${PCS_APRS_IGATE_SERVER}"
        printf "PCS_APRS_IGATE_MODE=%q\n" "${PCS_APRS_IGATE_MODE}"
        printf "PCS_APRS_IGATE_RF_TO_IS_FILTER=%q\n" "${PCS_APRS_IGATE_RF_TO_IS_FILTER}"
        printf "PCS_APRS_IGATE_IS_TO_RF_FILTER=%q\n" "${PCS_APRS_IGATE_IS_TO_RF_FILTER}"
        printf "PCS_APRS_IGATE_TX_PATH=%q\n" "${PCS_APRS_IGATE_TX_PATH}"
        printf "PCS_APRS_IGATE_TX_LIMIT_1M=%q\n" "${PCS_APRS_IGATE_TX_LIMIT_1M}"
        printf "PCS_APRS_IGATE_TX_LIMIT_5M=%q\n" "${PCS_APRS_IGATE_TX_LIMIT_5M}"
        printf "PCS_APRS_GPSD=%q\n" "${PCS_APRS_GPSD}"
        printf "PCS_APRS_GPSD_HOST=%q\n" "${PCS_APRS_GPSD_HOST}"
        printf "PCS_APRS_GPSD_PORT=%q\n" "${PCS_APRS_GPSD_PORT}"
        printf "PCS_APRS_BEACON=%q\n" "${PCS_APRS_BEACON}"
        printf "PCS_APRS_BEACON_TYPE=%q\n" "${PCS_APRS_BEACON_TYPE}"
        printf "PCS_APRS_BEACON_INTERVAL=%q\n" "${PCS_APRS_BEACON_INTERVAL}"
        printf "PCS_APRS_BEACON_PATH=%q\n" "${PCS_APRS_BEACON_PATH}"
        printf "PCS_APRS_BEACON_SENDTO=%q\n" "${PCS_APRS_BEACON_SENDTO}"
        printf "PCS_APRS_BEACON_SYMBOL=%q\n" "${PCS_APRS_BEACON_SYMBOL}"
        printf "PCS_APRS_BEACON_OVERLAY=%q\n" "${PCS_APRS_BEACON_OVERLAY}"
        printf "PCS_APRS_BEACON_ALTITUDE=%q\n" "${PCS_APRS_BEACON_ALTITUDE}"
        printf "PCS_APRS_BEACON_COMMENT=%q\n" "${PCS_APRS_BEACON_COMMENT}"
        printf "PCS_APRS_DIGIPEAT=%q\n" "${PCS_APRS_DIGIPEAT}"
        printf "PCS_APRS_DIGIPEAT_MODE=%q\n" "${PCS_APRS_DIGIPEAT_MODE}"
        printf "PCS_APRS_DIGIPEAT_ALIAS=%q\n" "${PCS_APRS_DIGIPEAT_ALIAS}"
        printf "PCS_APRS_DIGIPEAT_ALIAS_PATTERN=%q\n" "${PCS_APRS_DIGIPEAT_ALIAS_PATTERN}"
        printf "PCS_APRS_DIGIPEAT_WIDE_PATTERN=%q\n" "${PCS_APRS_DIGIPEAT_WIDE_PATTERN}"
        printf "PCS_APRS_DIGIPEAT_PREEMPTIVE=%q\n" "${PCS_APRS_DIGIPEAT_PREEMPTIVE}"
        printf "PCS_APRS_DIGIPEAT_FILTER=%q\n" "${PCS_APRS_DIGIPEAT_FILTER}"
        printf "PCS_APRS_DIGIPEAT_DEDUPE_SECONDS=%q\n" "${PCS_APRS_DIGIPEAT_DEDUPE_SECONDS}"
        printf "PCS_APRS_TX_ENABLED=%q\n" "${PCS_APRS_TX_ENABLED}"
        printf "PCS_APRS_FX25_TX=%q\n" "${PCS_APRS_FX25_TX}"
        printf "PCS_APRS_LOGGING=%q\n" "${PCS_APRS_LOGGING}"
        printf "PCS_APRS_LOG_RETENTION_DAYS=%q\n" "${PCS_APRS_LOG_RETENTION_DAYS}"
        printf "PCS_APRS_DWAIT=%q\n" "${PCS_APRS_DWAIT}"
        printf "PCS_APRS_SLOTTIME=%q\n" "${PCS_APRS_SLOTTIME}"
        printf "PCS_APRS_PERSIST=%q\n" "${PCS_APRS_PERSIST}"
        printf "PCS_APRS_TXDELAY=%q\n" "${PCS_APRS_TXDELAY}"
        printf "PCS_APRS_TXTAIL=%q\n" "${PCS_APRS_TXTAIL}"
        printf "PCS_APRS_FULLDUP=%q\n" "${PCS_APRS_FULLDUP}"
        printf "PCS_APRS_RX_AUDIO_VALIDATED=%q\n" "${PCS_APRS_RX_AUDIO_VALIDATED}"
        printf "PCS_APRS_RADIO_CHANNEL_VALIDATED=%q\n" "${PCS_APRS_RADIO_CHANNEL_VALIDATED}"
        printf "PCS_APRS_PTT_VALIDATED=%q\n" "${PCS_APRS_PTT_VALIDATED}"
        printf "PCS_APRS_TX_AUDIO_VALIDATED=%q\n" "${PCS_APRS_TX_AUDIO_VALIDATED}"
        printf "PCS_APRS_TX_TIMING_VALIDATED=%q\n" "${PCS_APRS_TX_TIMING_VALIDATED}"
        printf "PCS_APRS_RADIO_INIT=%q\n" "${PCS_APRS_RADIO_INIT}"
        printf "PCS_APRS_RADIO_DEVICE=%q\n" "${PCS_APRS_RADIO_DEVICE}"
        printf "PCS_APRS_RADIO_BAUD=%q\n" "${PCS_APRS_RADIO_BAUD}"
        printf "PCS_APRS_RADIO_BANDWIDTH_KHZ=%q\n" "${PCS_APRS_RADIO_BANDWIDTH_KHZ}"
        printf "PCS_APRS_RADIO_TX_FREQUENCY_MHZ=%q\n" "${PCS_APRS_RADIO_TX_FREQUENCY_MHZ}"
        printf "PCS_APRS_RADIO_RX_FREQUENCY_MHZ=%q\n" "${PCS_APRS_RADIO_RX_FREQUENCY_MHZ}"
        printf "PCS_APRS_RADIO_TX_TONE=%q\n" "${PCS_APRS_RADIO_TX_TONE}"
        printf "PCS_APRS_RADIO_RX_TONE=%q\n" "${PCS_APRS_RADIO_RX_TONE}"
        printf "PCS_APRS_RADIO_SQUELCH=%q\n" "${PCS_APRS_RADIO_SQUELCH}"
        printf "PCS_APRS_RADIO_VOLUME=%q\n" "${PCS_APRS_RADIO_VOLUME}"
        printf "PCS_APRS_RADIO_PRE_DE_EMPHASIS=%q\n" "${PCS_APRS_RADIO_PRE_DE_EMPHASIS}"
        printf "PCS_APRS_RADIO_HIGH_PASS=%q\n" "${PCS_APRS_RADIO_HIGH_PASS}"
        printf "PCS_APRS_RADIO_LOW_PASS=%q\n" "${PCS_APRS_RADIO_LOW_PASS}"
        printf "PCS_APRS_RADIO_TX_TAIL=%q\n" "${PCS_APRS_RADIO_TX_TAIL}"
    } > "${INSTALL_CONFIG}"

    chmod 0600 "${INSTALL_CONFIG}"
}

choose_setup_mode() {
    local answer

    echo "Setup input mode:"
    echo "  ASK      - Ask questions as each setup step reaches them."
    echo "  ALL      - Ask setup questions up front, then run unattended where possible."
    echo "  DEFAULTS - Use current PCS defaults, then run unattended where possible."
    echo "  QUIT     - Exit without changing anything."
    echo

    while true; do
        read -r -p "Choose setup input mode [ASK/ALL/DEFAULTS/QUIT]: " answer
        answer="${answer:-ASK}"

        case "${answer}" in
            ask|ASK|Ask)
                PCS_SETUP_MODE="ASK"
                return 0
                ;;
            all|ALL|All)
                PCS_SETUP_MODE="ALL"
                return 0
                ;;
            defaults|DEFAULTS|Defaults|default|DEFAULT|Default)
                PCS_SETUP_MODE="DEFAULTS"
                return 0
                ;;
            quit|QUIT|Quit|q|Q)
                echo "Aborted."
                exit 0
                ;;
            *)
                echo "Please choose ASK, ALL, DEFAULTS, or QUIT."
                ;;
        esac
    done
}

collect_install_answers() {
    local usb_default
    local gps_default
    local gpsd_lan_default
    local pistar_default
    local aprs_default
    local meshtastic_default
    local gpio_lcd_default
    local gpio_leds_default
    local gpio_stats_default
    local gpio_fan_default
    local cellular_fallback_default

    case "${PCS_SETUP_MODE}" in
        DEFAULTS)
            PCS_CELLULAR_PROFILE="${PCS_CELLULAR_PROFILE_DEFAULT}"
            PCS_CELLULAR_APN="${PCS_CELLULAR_APN_DEFAULT}"
            PCS_CELLULAR_ROUTE_METRIC="${PCS_CELLULAR_ROUTE_METRIC_DEFAULT}"
            PCS_CELLULAR_FALLBACK_MODE="${PCS_CELLULAR_FALLBACK_MODE_DEFAULT}"
            PCS_SAMBA_USER="${USER}"
            PCS_SAMBA_WORKGROUP="${PCS_SAMBA_WORKGROUP_DEFAULT}"
            if [[ -z "${PCS_SAMBA_PASSWORD}" ]]; then
                ask_secret_confirm "Samba password for ${PCS_SAMBA_WORKGROUP}\\${PCS_SAMBA_USER}" PCS_SAMBA_PASSWORD
            fi
            PCS_SETUP_USB_PRIMARY="yes"
            PCS_SETUP_USB_DEVICE="auto"
            PCS_SETUP_WWAN_GPS="no"
            PCS_SETUP_GPSD_LAN="no"
            PCS_SETUP_PISTAR="no"
            PCS_SETUP_APRS="no"
            PCS_SETUP_MESHTASTIC="no"
            PCS_SETUP_GPIO_LCD="no"
            PCS_SETUP_GPIO_LEDS="no"
            PCS_SETUP_GPIO_STATS="no"
            PCS_SETUP_GPIO_FAN="no"
            ;;
        ALL)
            PCS_CELLULAR_PROFILE="$(ask_value "Cellular profile name" "${PCS_CELLULAR_PROFILE}")"
            PCS_CELLULAR_APN="$(ask_value "Cellular APN" "${PCS_CELLULAR_APN}")"
            cellular_fallback_default="no"
            [[ "${PCS_CELLULAR_FALLBACK_MODE}" == "wifi-fallback" ]] && cellular_fallback_default="yes"
            if [[ "$(ask_yes_no "Automatically use cellular when Wi-Fi is unavailable?" "${cellular_fallback_default}")" == "yes" ]]; then
                PCS_CELLULAR_FALLBACK_MODE="wifi-fallback"
            else
                PCS_CELLULAR_FALLBACK_MODE="manual"
            fi
            PCS_SAMBA_USER="$(ask_value "Samba username" "${PCS_SAMBA_USER}")"
            PCS_SAMBA_WORKGROUP="$(ask_value "Samba workgroup" "${PCS_SAMBA_WORKGROUP}")"
            if [[ -z "${PCS_SAMBA_PASSWORD}" ]]; then
                ask_secret_confirm "Samba password for ${PCS_SAMBA_WORKGROUP}\\${PCS_SAMBA_USER}" PCS_SAMBA_PASSWORD
            fi
            usb_default="${PCS_SETUP_USB_PRIMARY}"
            gps_default="${PCS_SETUP_WWAN_GPS}"
            gpsd_lan_default="${PCS_SETUP_GPSD_LAN}"
            pistar_default="${PCS_SETUP_PISTAR}"
            aprs_default="${PCS_SETUP_APRS}"
            meshtastic_default="${PCS_SETUP_MESHTASTIC}"
            gpio_lcd_default="${PCS_SETUP_GPIO_LCD}"
            gpio_leds_default="${PCS_SETUP_GPIO_LEDS}"
            gpio_stats_default="${PCS_SETUP_GPIO_STATS}"
            gpio_fan_default="${PCS_SETUP_GPIO_FAN}"
            [[ "${usb_default}" == "ask" ]] && usb_default="yes"
            [[ "${gps_default}" == "ask" ]] && gps_default="no"
            [[ "${gpsd_lan_default}" == "ask" ]] && gpsd_lan_default="no"
            [[ "${pistar_default}" == "ask" ]] && pistar_default="no"
            [[ "${aprs_default}" == "ask" ]] && aprs_default="no"
            [[ "${aprs_default}" == "staged" ]] && aprs_default="yes"
            [[ "${meshtastic_default}" == "ask" ]] && meshtastic_default="no"
            [[ "${meshtastic_default}" == "staged" ]] && meshtastic_default="yes"
            [[ "${gpio_lcd_default}" == "ask" ]] && gpio_lcd_default="no"
            [[ "${gpio_leds_default}" == "ask" ]] && gpio_leds_default="no"
            [[ "${gpio_stats_default}" == "ask" ]] && gpio_stats_default="no"
            [[ "${gpio_fan_default}" == "ask" ]] && gpio_fan_default="no"
            PCS_SETUP_USB_PRIMARY="$(ask_yes_no "Configure detected USB storage as PCS-Share primary storage?" "${usb_default}")"
            if [[ "${PCS_SETUP_USB_PRIMARY}" == "yes" ]]; then
                PCS_SETUP_USB_DEVICE="$(ask_value "USB storage device or UUID" "${PCS_SETUP_USB_DEVICE}")"
            else
                PCS_SETUP_USB_DEVICE="auto"
            fi
            PCS_SETUP_WWAN_GPS="$(ask_yes_no "Configure WWAN modem NMEA GPS during setup?" "${gps_default}")"
            PCS_SETUP_GPSD_LAN="$(ask_yes_no "Share GPSD with trusted PCS LAN clients?" "${gpsd_lan_default}")"
            PCS_SETUP_PISTAR="$(ask_yes_no "Include a Pi-Star hotspot in PCS monitoring and local-access links?" "${pistar_default}")"
            PCS_SETUP_APRS="$(ask_yes_no "Stage optional Dire Wolf / APRS software without enabling radio or RF transmit?" "${aprs_default}")"
            PCS_SETUP_MESHTASTIC="$(ask_yes_no "Stage optional Meshtastic USB/Bluetooth support without connecting to or configuring a radio?" "${meshtastic_default}")"
            PCS_SETUP_GPIO_LCD="$(ask_yes_no "Install and start the optional 16x2 HD44780 LCD status display?" "${gpio_lcd_default}")"
            PCS_SETUP_GPIO_LEDS="$(ask_yes_no "Install and start the optional six-pixel WS2812 status indicators?" "${gpio_leds_default}")"
            PCS_SETUP_GPIO_STATS="$(ask_yes_no "Install and start the optional MAX7219 LED matrix statistics display?" "${gpio_stats_default}")"
            PCS_SETUP_GPIO_FAN="$(ask_yes_no "Install GPIO18 hardware PWM thermal fan control?" "${gpio_fan_default}")"
            ;;
        ASK)
            PCS_CELLULAR_PROFILE="$(ask_value "Cellular profile name" "${PCS_CELLULAR_PROFILE}")"
            PCS_CELLULAR_APN="$(ask_value "Cellular APN" "${PCS_CELLULAR_APN}")"
            cellular_fallback_default="no"
            [[ "${PCS_CELLULAR_FALLBACK_MODE}" == "wifi-fallback" ]] && cellular_fallback_default="yes"
            if [[ "$(ask_yes_no "Automatically use cellular when Wi-Fi is unavailable?" "${cellular_fallback_default}")" == "yes" ]]; then
                PCS_CELLULAR_FALLBACK_MODE="wifi-fallback"
            else
                PCS_CELLULAR_FALLBACK_MODE="manual"
            fi
            PCS_SAMBA_USER="${USER}"
            PCS_SAMBA_WORKGROUP="${PCS_SAMBA_WORKGROUP:-${PCS_SAMBA_WORKGROUP_DEFAULT}}"
            PCS_SETUP_USB_PRIMARY="ask"
            PCS_SETUP_USB_DEVICE="auto"
            PCS_SETUP_WWAN_GPS="ask"
            PCS_SETUP_GPSD_LAN="ask"
            PCS_SETUP_APRS="ask"
            PCS_SETUP_MESHTASTIC="ask"
            PCS_SETUP_GPIO_LCD="ask"
            PCS_SETUP_GPIO_LEDS="ask"
            PCS_SETUP_GPIO_STATS="ask"
            PCS_SETUP_GPIO_FAN="ask"
            pistar_default="${PCS_SETUP_PISTAR}"
            [[ "${pistar_default}" == "ask" ]] && pistar_default="no"
            PCS_SETUP_PISTAR="$(ask_yes_no "Include a Pi-Star hotspot in PCS monitoring and local-access links?" "${pistar_default}")"
            ;;
    esac

    export PCS_INSTALL_CONFIG="${INSTALL_CONFIG}"
    export PCS_CELLULAR_PROFILE
    export PCS_CELLULAR_APN
    export PCS_CELLULAR_ROUTE_METRIC
    export PCS_CELLULAR_FALLBACK_MODE
    export PCS_SAMBA_USER
    export PCS_SAMBA_WORKGROUP
    export PCS_SAMBA_PASSWORD
    export PCS_SETUP_USB_PRIMARY
    export PCS_SETUP_USB_DEVICE
    export PCS_SETUP_WWAN_GPS
    export PCS_SETUP_GPSD_LAN
    export PCS_SETUP_PISTAR
    export PCS_SETUP_APRS
    export PCS_SETUP_MESHTASTIC
    export PCS_SETUP_GPIO_LCD
    export PCS_SETUP_GPIO_LEDS
    export PCS_SETUP_GPIO_STATS
    export PCS_SETUP_GPIO_FAN

    if [[ "${PCS_SETUP_MODE}" == "ASK" ]]; then
        unset PCS_ROUTER_WAN_SHARE_CONFIRM
        unset PCS_ASSUME_YES
    else
        export PCS_ROUTER_WAN_SHARE_CONFIRM="yes"
        export PCS_ASSUME_YES="1"
    fi
}

confirm_install_answers() {
    local answer
    local samba_password_status="not provided"

    if [[ -n "${PCS_SAMBA_PASSWORD}" ]]; then
        samba_password_status="provided; not written to config"
    fi

    echo
    echo "PCS setup answers:"
    echo "  Setup mode:         ${PCS_SETUP_MODE}"
    echo "  Cellular profile:   ${PCS_CELLULAR_PROFILE}"
    echo "  Cellular APN:       ${PCS_CELLULAR_APN}"
    echo "  Cellular metric:    ${PCS_CELLULAR_ROUTE_METRIC}"
    echo "  Cellular fallback:  ${PCS_CELLULAR_FALLBACK_MODE}"
    echo "  Samba username:     ${PCS_SAMBA_USER}"
    echo "  Samba workgroup:    ${PCS_SAMBA_WORKGROUP}"
    echo "  Samba password:     ${samba_password_status}"
    echo "  USB primary policy: ${PCS_SETUP_USB_PRIMARY}"
    echo "  USB device/UUID:    ${PCS_SETUP_USB_DEVICE}"
    echo "  WWAN GPS policy:    ${PCS_SETUP_WWAN_GPS}"
    echo "  LAN GPSD policy:    ${PCS_SETUP_GPSD_LAN}"
    echo "  Pi-Star monitoring: ${PCS_SETUP_PISTAR}"
    echo "  Dire Wolf / APRS:   ${PCS_SETUP_APRS}"
    echo "  Meshtastic BLE:      ${PCS_SETUP_MESHTASTIC}"
    echo "  HD44780 LCD:         ${PCS_SETUP_GPIO_LCD}"
    echo "  WS2812 indicators:   ${PCS_SETUP_GPIO_LEDS}"
    echo "  MAX7219 LED matrix: ${PCS_SETUP_GPIO_STATS}"
    echo "  GPIO18 PWM fan:     ${PCS_SETUP_GPIO_FAN}"
    echo

    if [[ "${PCS_SETUP_MODE}" == "ASK" ]]; then
        answer="$(ask_yes_no "Proceed with PCS base setup? Additional prompts may appear as each step runs." "no")"
    else
        answer="$(ask_yes_no "Proceed with unattended PCS setup using these answers?" "no")"
    fi

    if [[ "${answer}" != "yes" ]]; then
        echo "Aborted."
        exit 0
    fi
}

echo
echo "=== PCS Base Setup ==="
echo
echo "Repository: ${REPO_DIR}"
echo
echo "This script configures the current Raspberry Pi OS install for PCS baseline use."
echo
echo "It will run:"
echo "  - Dependency installer"
echo "  - Client LAN / AP handoff setup on eth0"
echo "  - Optional password-assisted Pi-Star coordinated shutdown pairing as soon as the PCS LAN is ready"
echo "  - RTC setup"
echo "  - Cellular profile setup with optional automatic Wi-Fi fallback"
echo "  - Samba bootstrap share setup"
echo "  - Samba SD-card backup share setup"
echo "  - Optional USB primary share setup, if USB storage is present"
echo "  - Chrony LAN NTP setup"
echo "  - Optional WWAN modem NMEA GPS setup, if WWAN GPS hardware is present"
echo "  - Optional LAN-only GPSD sharing for trusted PCS devices"
echo "  - Optional Pi-Star monitoring and local-access links"
echo "  - Optional hardware-safe Dire Wolf / APRS software staging"
echo "  - Optional hardware-safe Meshtastic USB/Bluetooth MQTT software staging"
echo "  - Optional six-pixel WS2812 status indicators"
echo "  - Optional MAX7219 LED matrix statistics display"
echo "  - Optional GPIO18 hardware PWM thermal fan control"
echo "  - Cockpit/systemd restart button install"
echo "  - PCS public homepage and authenticated control panel setup"
echo "  - Legacy port 8080 admin compatibility redirect"
echo "  - Final PCS status and self-test"
echo
echo "It will not automatically configure:"
echo "  - EM7565 firmware, USB composition, antenna hardware, or modem-side AT changes"
echo
echo "WWAN modem GPS can be configured as an optional hardware step if the modem is present."
echo

choose_setup_mode

collect_install_answers
confirm_install_answers
write_install_config

echo
echo "Install config written:"
echo "  ${INSTALL_CONFIG}"
echo

sudo -v
cd "${REPO_DIR}"

run_step() {
    local name="$1"
    local command="$2"

    echo
    echo "============================================================"
    echo "STEP: ${name}"
    echo "============================================================"
    echo

    bash -c "${command}"
}

run_optional_step() {
    local name="$1"
    local command="$2"

    echo
    echo "============================================================"
    echo "OPTIONAL STEP: ${name}"
    echo "============================================================"
    echo

    if bash -c "${command}"; then
        echo
        echo "Optional step completed: ${name}"
    else
        echo
        echo "WARNING: Optional step failed or was skipped: ${name}"
        echo "Continuing PCS base setup."
    fi
}

ensure_executable() {
    local script="$1"

    if [[ -f "${script}" ]]; then
        chmod +x "${script}"
    else
        echo "ERROR: Missing script: ${script}"
        exit 1
    fi
}

echo
echo "Making PCS scripts executable..."

ensure_executable "scripts/install-dependencies.sh"
ensure_executable "scripts/pcs-rtc-seed.sh"
ensure_executable "scripts/test-time-source-failover.sh"
ensure_executable "scripts/setup-rtc.sh"
ensure_executable "scripts/setup-router-wan-share.sh"
ensure_executable "scripts/setup-cellular-profile.sh"
ensure_executable "scripts/pcs_cellular_fallback.py"
ensure_executable "scripts/setup-test-samba-share.sh"
ensure_executable "scripts/setup-samba-backup-share.sh"
ensure_executable "scripts/setup-usb-primary-share.sh"
ensure_executable "scripts/setup-chrony-lan-ntp.sh"
ensure_executable "scripts/restart-pcs-services.sh"
ensure_executable "scripts/setup-pcs-control-panel.sh"
ensure_executable "scripts/setup-dashboard-redirect.sh"
ensure_executable "scripts/setup-wwan-gps-nmea.sh"
ensure_executable "scripts/setup-gpsd-lan-proxy.sh"
ensure_executable "scripts/setup-pistar-shutdown.sh"
ensure_executable "scripts/setup-direwolf-aprs.sh"
ensure_executable "scripts/setup-meshtastic-bluetooth.sh"
ensure_executable "scripts/setup-gpio-lcd.sh"
ensure_executable "scripts/setup-gpio-leds.sh"
ensure_executable "scripts/setup-gpio-stats.sh"
ensure_executable "scripts/setup-gpio-fan.sh"
ensure_executable "scripts/pcs-gpio-startup.sh"
ensure_executable "scripts/pcs-aprs-kiss-firewall.sh"
ensure_executable "scripts/pcs-aprs-audio.sh"
ensure_executable "scripts/pcs_sa818.py"
ensure_executable "scripts/test-direwolf-aprs-software.sh"
ensure_executable "scripts/pcs_aprs_telemetry.py"
ensure_executable "scripts/pcs-wwan-gps-nmea-start.py"
ensure_executable "scripts/pcs-web-action.sh"
ensure_executable "scripts/sync-pcs-share-to-backup.sh"
ensure_executable "scripts/pcs-self-test.sh"
ensure_executable "scripts/pcs-status.sh"

if [[ -d "web/pcs-control-panel" ]]; then
    chmod +x web/pcs-control-panel/*.py 2>/dev/null || true
fi

run_step "Install dependencies" "PCS_DEFER_MODEMMANAGER_START=1 ./scripts/install-dependencies.sh"

run_step "Configure client LAN/AP handoff on eth0" "./scripts/setup-router-wan-share.sh"

if [[ "${PCS_SETUP_PISTAR}" == "yes" ]]; then
    run_optional_step \
        "Pair Pi-Star coordinated shutdown" \
        "PCS_PISTAR_PAIR_CONFIRM=yes ./scripts/setup-pistar-shutdown.sh"
else
    echo
    echo "Pi-Star monitoring is disabled; skipping coordinated shutdown pairing."
fi

run_step "Configure RTC" "./scripts/setup-rtc.sh"

run_step "Configure cellular profile and fallback policy" "./scripts/setup-cellular-profile.sh"

echo
echo "============================================================"
echo "STEP: Configure Samba bootstrap share"
echo "============================================================"
echo
echo "This step uses the Samba password collected up front in ALL/DEFAULTS mode."
echo "In ASK mode, it may ask you to set or confirm a Samba password."
echo "The Samba password is not stored in the repository."
echo

./scripts/setup-test-samba-share.sh

run_step "Configure Samba SD-card backup share" "./scripts/setup-samba-backup-share.sh"

echo
echo "============================================================"
echo "OPTIONAL STEP: Configure USB primary Samba share"
echo "============================================================"
echo

USB_DEVICE=""
USB_DEVICE_REASON=""

detect_usb_storage_candidates() {
    while read -r name type fstype pkname rm hotplug tran; do
        [[ -z "${name}" ]] && continue
        [[ -z "${fstype}" ]] && continue

        parent_tran="${tran}"
        parent_rm="${rm}"
        parent_hotplug="${hotplug}"

        if [[ "${type}" == "part" && -n "${pkname}" ]]; then
            parent_device="${pkname}"
            if [[ "${parent_device}" != /dev/* ]]; then
                parent_device="/dev/${parent_device}"
            fi

            parent_tran="$(lsblk -dnro TRAN "${parent_device}" 2>/dev/null || true)"
            parent_rm="$(lsblk -dnro RM "${parent_device}" 2>/dev/null || true)"
            parent_hotplug="$(lsblk -dnro HOTPLUG "${parent_device}" 2>/dev/null || true)"
        fi

        if [[ "${parent_tran}" == "usb" || "${parent_rm}" == "1" || "${parent_hotplug}" == "1" ]]; then
            echo "${name}"
        fi
    done < <(lsblk -rpno NAME,TYPE,FSTYPE,PKNAME,RM,HOTPLUG,TRAN 2>/dev/null)
}

echo "Scanning for USB storage..."

if [[ -n "${PCS_SETUP_USB_DEVICE}" && "${PCS_SETUP_USB_DEVICE}" != "auto" ]]; then
    USB_DEVICE="${PCS_SETUP_USB_DEVICE}"
    USB_DEVICE_REASON="selected in setup answers"
elif sudo blkid -U "${USB_UUID_DEFAULT}" >/dev/null 2>&1; then
    USB_DEVICE="$(sudo blkid -U "${USB_UUID_DEFAULT}")"
    USB_DEVICE_REASON="matched default UUID ${USB_UUID_DEFAULT}"
else
    USB_CANDIDATES=()

    for usb_scan_attempt in $(seq 1 12); do
        udevadm settle --timeout=5 >/dev/null 2>&1 || true
        mapfile -t USB_CANDIDATES < <(detect_usb_storage_candidates | sort -u)

        if [[ "${#USB_CANDIDATES[@]}" -gt 0 ]]; then
            break
        fi

        if [[ "${usb_scan_attempt}" -lt 12 ]]; then
            echo "No USB storage filesystem detected yet; waiting... ${usb_scan_attempt}/12"
            sleep 5
        fi
    done

    if [[ "${#USB_CANDIDATES[@]}" -eq 1 ]]; then
        USB_DEVICE="${USB_CANDIDATES[0]}"
        USB_DEVICE_REASON="only detected removable/USB storage filesystem"
    elif [[ "${#USB_CANDIDATES[@]}" -gt 1 ]]; then
        echo "Multiple removable/USB storage filesystems were detected:"
        echo

        for i in "${!USB_CANDIDATES[@]}"; do
            device="${USB_CANDIDATES[$i]}"
            printf "  %s) %s\n" "$((i + 1))" "${device}"
            lsblk -no NAME,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS "${device}" 2>/dev/null || true
        done

        echo
        if [[ "${PCS_SETUP_USB_PRIMARY}" == "yes" || "${PCS_SETUP_USB_PRIMARY}" == "no" ]]; then
            echo "Multiple USB filesystems found; USB primary policy is ${PCS_SETUP_USB_PRIMARY}."
            echo "Skipping automatic selection because the target is ambiguous."
        else
            read -r -p "Select USB filesystem number for PCS-Share, or press Enter to skip: " usb_choice

            if [[ "${usb_choice}" =~ ^[0-9]+$ ]] \
                && (( usb_choice >= 1 )) \
                && (( usb_choice <= ${#USB_CANDIDATES[@]} )); then
                USB_DEVICE="${USB_CANDIDATES[$((usb_choice - 1))]}"
                USB_DEVICE_REASON="selected from detected removable/USB storage filesystems"
            else
                echo "No valid USB filesystem selected."
            fi
        fi
    fi
fi

if [[ -n "${USB_DEVICE}" ]]; then
    USB_DEVICE_DISPLAY="${USB_DEVICE}"
    if [[ "${USB_DEVICE_DISPLAY}" != /dev/* ]]; then
        USB_DEVICE_DISPLAY="$(sudo blkid -U "${USB_DEVICE}" 2>/dev/null || true)"
    fi

    echo
    echo "USB storage candidate:"
    echo "  Device: ${USB_DEVICE}"
    echo "  Reason: ${USB_DEVICE_REASON}"
    echo
    if [[ -n "${USB_DEVICE_DISPLAY}" ]]; then
        lsblk -o NAME,PATH,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,TRAN "${USB_DEVICE_DISPLAY}" 2>/dev/null || true
    else
        echo "  ${USB_DEVICE} was not resolved yet; setup-usb-primary-share.sh will validate it."
    fi
    echo

    if [[ "${PCS_SETUP_USB_PRIMARY}" == "yes" || "${PCS_SETUP_USB_PRIMARY}" == "no" ]]; then
        usb_answer="${PCS_SETUP_USB_PRIMARY}"
        echo "Configure this USB device as PCS-Share primary storage? [Y/N] ${usb_answer}"
    else
        usb_answer="$(ask_yes_no "Configure this USB device as PCS-Share primary storage?" "yes")"
    fi

    case "${usb_answer}" in
        n|N|no|NO|No)
            echo "Skipping USB primary share setup."
            ;;
        *)
            ./scripts/setup-usb-primary-share.sh "${USB_DEVICE}"
            ;;
    esac
else
    echo "No removable/USB storage filesystem was detected."
    echo "Skipping USB primary share setup."
    echo "You can run this later with a specific device, for example:"
    echo "  ./scripts/setup-usb-primary-share.sh /dev/sda1"
fi

run_step "Configure Chrony LAN NTP" "./scripts/setup-chrony-lan-ntp.sh"

echo
echo "============================================================"
echo "OPTIONAL STEP: Configure WWAN modem NMEA GPS"
echo "============================================================"
echo
echo "This optional step configures:"
echo "  - WWAN modem GPS NMEA on /dev/ttyUSB1"
echo "  - gpsd reading /dev/ttyUSB1"
echo "  - Chrony reading gpsd SHM refclock 0"
echo
echo "Use this only when the WWAN modem and GPS antenna are installed."
echo

if [[ -x "./scripts/setup-wwan-gps-nmea.sh" ]]; then
    if [[ "${PCS_SETUP_WWAN_GPS}" == "yes" || "${PCS_SETUP_WWAN_GPS}" == "no" ]]; then
        gps_answer="${PCS_SETUP_WWAN_GPS}"
        echo "Configure WWAN modem NMEA GPS now? [Y/N] ${gps_answer}"
    else
        gps_answer="$(ask_yes_no "Configure WWAN modem NMEA GPS now?" "no")"
    fi

    case "${gps_answer}" in
        y|Y|yes|YES|Yes)
            if PCS_WWAN_GPS_CONFIRM=yes ./scripts/setup-wwan-gps-nmea.sh; then
                echo "WWAN modem NMEA GPS setup completed."
            else
                echo
                echo "WARNING: WWAN modem NMEA GPS setup failed."
                echo "Continuing PCS base setup so dashboard/control panel installation can still complete."
                echo "You can retry GPS setup later with:"
                echo "  ./scripts/setup-wwan-gps-nmea.sh"
            fi
            ;;
        *)
            echo "Skipping WWAN modem NMEA GPS setup."
            echo "You can run this later:"
            echo "  ./scripts/setup-wwan-gps-nmea.sh"
            ;;
    esac
else
    echo "WARNING: scripts/setup-wwan-gps-nmea.sh not found or not executable."
    echo "Skipping WWAN modem NMEA GPS setup."
fi

echo
echo "============================================================"
echo "OPTIONAL STEP: Share GPSD with trusted PCS LAN clients"
echo "============================================================"
echo
echo "This publishes GPSD only at 10.42.0.1:2947 for devices on the PCS LAN."
echo "GPSD itself remains bound to localhost."
echo

if [[ "${PCS_SETUP_GPSD_LAN}" == "yes" || "${PCS_SETUP_GPSD_LAN}" == "no" ]]; then
    gpsd_lan_answer="${PCS_SETUP_GPSD_LAN}"
    echo "Configure the LAN-only GPSD proxy now? [Y/N] ${gpsd_lan_answer}"
else
    gpsd_lan_answer="$(ask_yes_no "Configure the LAN-only GPSD proxy now?" "no")"
fi

case "${gpsd_lan_answer}" in
    y|Y|yes|YES|Yes)
        if PCS_GPSD_LAN_CONFIRM=yes ./scripts/setup-gpsd-lan-proxy.sh; then
            echo "LAN-only GPSD proxy setup completed."
        else
            echo
            echo "WARNING: LAN-only GPSD proxy setup failed."
            echo "You can retry it later with:"
            echo "  ./scripts/setup-gpsd-lan-proxy.sh"
        fi
        ;;
    *)
        echo "Skipping LAN-only GPSD proxy setup."
        echo "You can run this later:"
        echo "  ./scripts/setup-gpsd-lan-proxy.sh"
        ;;
esac

echo
echo "============================================================"
echo "OPTIONAL STEP: Install 16x2 HD44780 LCD status display"
echo "============================================================"
echo
echo "This installs the GPIO-only LCD driver and starts pcs-gpio-lcd.service."
echo "Select it only when the HD44780-compatible 16x2 display is installed."
echo

if [[ "${PCS_SETUP_GPIO_LCD}" == "yes" || "${PCS_SETUP_GPIO_LCD}" == "no" ]]; then
    gpio_lcd_answer="${PCS_SETUP_GPIO_LCD}"
else
    gpio_lcd_answer="$(ask_yes_no "Install and start the optional 16x2 HD44780 LCD status display?" "no")"
fi
PCS_SETUP_GPIO_LCD="${gpio_lcd_answer}"
export PCS_SETUP_GPIO_LCD
write_install_config

case "${gpio_lcd_answer}" in
    y|Y|yes|YES|Yes)
        run_optional_step \
            "Install 16x2 HD44780 LCD status display" \
            "./scripts/setup-gpio-lcd.sh --install"
        ;;
    *)
        echo "Skipping 16x2 HD44780 LCD status display."
        echo "You can install it later with:"
        echo "  ./scripts/setup-gpio-lcd.sh --install"
        ;;
esac

echo
echo "============================================================"
echo "OPTIONAL STEP: Install six-pixel WS2812 status indicators"
echo "============================================================"
echo
echo "This installs the isolated WS2812 driver and starts pcs-gpio-leds.service."
echo "Select it only when the six-pixel chain is connected to GPIO21 through the"
echo "74AHCT125 level shifter. GPIO21 PCM must not be shared with an I2S device."
echo

if [[ "${PCS_SETUP_GPIO_LEDS}" == "yes" || "${PCS_SETUP_GPIO_LEDS}" == "no" ]]; then
    gpio_leds_answer="${PCS_SETUP_GPIO_LEDS}"
else
    gpio_leds_answer="$(ask_yes_no "Install and start the optional six-pixel WS2812 status indicators?" "no")"
fi
PCS_SETUP_GPIO_LEDS="${gpio_leds_answer}"
export PCS_SETUP_GPIO_LEDS
write_install_config

case "${gpio_leds_answer}" in
    y|Y|yes|YES|Yes)
        run_optional_step \
            "Install six-pixel WS2812 status indicators" \
            "./scripts/setup-gpio-leds.sh --install"
        ;;
    *)
        echo "Skipping six-pixel WS2812 status indicators."
        echo "You can install them later with:"
        echo "  ./scripts/setup-gpio-leds.sh --install"
        ;;
esac

echo
echo "============================================================"
echo "OPTIONAL STEP: Install GPIO18 hardware PWM thermal fan control"
echo "============================================================"
echo
echo "This disables unused onboard analog audio, enables PWM0 on GPIO18, and"
echo "installs a fail-safe temperature-controlled fan service. A reboot is normally"
echo "required before the hardware PWM interface and service become active."
echo

if [[ "${PCS_SETUP_GPIO_FAN}" == "yes" || "${PCS_SETUP_GPIO_FAN}" == "no" ]]; then
    gpio_fan_answer="${PCS_SETUP_GPIO_FAN}"
else
    gpio_fan_answer="$(ask_yes_no "Install GPIO18 hardware PWM thermal fan control?" "no")"
fi
PCS_SETUP_GPIO_FAN="${gpio_fan_answer}"
export PCS_SETUP_GPIO_FAN
write_install_config

case "${gpio_fan_answer}" in
    y|Y|yes|YES|Yes)
        run_optional_step \
            "Install GPIO18 hardware PWM thermal fan control" \
            "./scripts/setup-gpio-fan.sh --install"
        ;;
    *)
        echo "Skipping GPIO18 hardware PWM thermal fan control."
        echo "You can install it later with:"
        echo "  ./scripts/setup-gpio-fan.sh --install"
        ;;
esac

echo
echo "============================================================"
echo "OPTIONAL STEP: Stage Dire Wolf / APRS software"
echo "============================================================"
echo
echo "This installs Dire Wolf but leaves its service disabled, with no callsign,"
echo "APRS-IS credential, audio device, PTT method, beacon, or RF transmit path."
echo

if [[ "${PCS_SETUP_APRS}" == "yes" || "${PCS_SETUP_APRS}" == "no" ]]; then
    aprs_answer="${PCS_SETUP_APRS}"
elif [[ "${PCS_SETUP_APRS}" == "staged" ]]; then
    aprs_answer="yes"
else
    aprs_answer="$(ask_yes_no "Stage Dire Wolf / APRS software now?" "no")"
fi

case "${aprs_answer}" in
    y|Y|yes|YES|Yes)
        if ./scripts/setup-direwolf-aprs.sh --prepare-uart; then
            echo "PCS APRS UART preparation completed."
        else
            echo
            echo "WARNING: PCS APRS UART preparation failed."
            echo "RF activation will remain blocked until this succeeds:"
            echo "  ./scripts/setup-direwolf-aprs.sh --prepare-uart"
        fi
        if ./scripts/setup-direwolf-aprs.sh --prepare; then
            echo "Dire Wolf / APRS software staging completed."
        else
            echo
            echo "WARNING: Dire Wolf / APRS software staging failed."
            echo "You can retry it later with:"
            echo "  ./scripts/setup-direwolf-aprs.sh --prepare"
        fi
        ;;
    *)
        echo "Skipping Dire Wolf / APRS software staging."
        echo "You can run it later:"
        echo "  ./scripts/setup-direwolf-aprs.sh --prepare"
        ;;
esac

echo
echo "============================================================"
echo "OPTIONAL STEP: Stage Meshtastic USB/Bluetooth support"
echo "============================================================"
echo
echo "This installs a pinned Meshtastic BLE/MQTT client and privacy-preserving"
echo "gateway status collector. The persistent gateway stays disabled until a"
echo "paired node and MQTT broker are explicitly configured."
echo

if [[ "${PCS_SETUP_MESHTASTIC}" == "yes" || "${PCS_SETUP_MESHTASTIC}" == "no" ]]; then
    meshtastic_answer="${PCS_SETUP_MESHTASTIC}"
elif [[ "${PCS_SETUP_MESHTASTIC}" == "staged" ]]; then
    meshtastic_answer="yes"
else
    meshtastic_answer="$(ask_yes_no "Stage Meshtastic USB/Bluetooth support now?" "no")"
fi

case "${meshtastic_answer}" in
    y|Y|yes|YES|Yes)
        if ./scripts/setup-meshtastic-bluetooth.sh --prepare; then
            PCS_SETUP_MESHTASTIC="staged"
            echo "Meshtastic USB/Bluetooth software staging completed."
        else
            echo
            echo "WARNING: Meshtastic USB/Bluetooth software staging failed."
            echo "You can retry it later with:"
            echo "  ./scripts/setup-meshtastic-bluetooth.sh --prepare"
        fi
        ;;
    *)
        PCS_SETUP_MESHTASTIC="no"
        echo "Skipping Meshtastic USB/Bluetooth software staging."
        echo "You can run it later with:"
        echo "  ./scripts/setup-meshtastic-bluetooth.sh --prepare"
        ;;
esac
export PCS_SETUP_MESHTASTIC
write_install_config

echo
echo "--- Ensure ModemManager is running for dashboard and self-test ---"
sudo systemctl start ModemManager 2>/dev/null || true

echo
echo "============================================================"
echo "OPTIONAL STEP: Install MAX7219 LED matrix statistics display"
echo "============================================================"
echo
echo "This enables SPI0 when necessary, installs the matrix driver, and starts the"
echo "SPI-only pcs-gpio-stats.service. Select it only when the MAX7219 is installed."
echo

if [[ "${PCS_SETUP_GPIO_STATS}" == "yes" || "${PCS_SETUP_GPIO_STATS}" == "no" ]]; then
    gpio_stats_answer="${PCS_SETUP_GPIO_STATS}"
else
    gpio_stats_answer="$(ask_yes_no "Install and start the MAX7219 LED matrix statistics display?" "no")"
fi
PCS_SETUP_GPIO_STATS="${gpio_stats_answer}"
export PCS_SETUP_GPIO_STATS
write_install_config

case "${gpio_stats_answer}" in
    y|Y|yes|YES|Yes)
        run_optional_step \
            "Install MAX7219 LED matrix statistics display" \
            "./scripts/setup-gpio-stats.sh --install"
        ;;
    *)
        echo "Skipping MAX7219 LED matrix statistics display."
        echo "You can install it later with:"
        echo "  ./scripts/setup-gpio-stats.sh --install"
        ;;
esac

echo
echo "============================================================"
echo "STEP: Install Cockpit/systemd restart button"
echo "============================================================"
echo

if [[ -f "systemd/pcs-restart-services.service" ]]; then
    echo "Installing pcs-restart-services.service..."
    sudo cp systemd/pcs-restart-services.service /etc/systemd/system/pcs-restart-services.service
    sudo systemctl daemon-reload

    echo "Disabling pcs-restart-services.service for boot."
    echo "It is intended to be started manually, not run automatically."
    sudo systemctl disable pcs-restart-services.service >/dev/null 2>&1 || true

    echo "Installed service restart action:"
    echo "  pcs-restart-services.service"
else
    echo "WARNING: systemd/pcs-restart-services.service not found."
    echo "Skipping Cockpit/systemd restart button install."
fi

run_step "Install PCS Control Panel" "./scripts/setup-pcs-control-panel.sh"

echo
echo "============================================================"
echo "STEP: Final PCS status"
echo "============================================================"
echo

./scripts/pcs-status.sh || true

echo
echo "============================================================"
echo "STEP: PCS self-test"
echo "============================================================"
echo

if ./scripts/pcs-self-test.sh; then
    echo
    echo "PCS base setup completed and self-test passed."
else
    echo
    echo "PCS base setup completed, but self-test reported warnings or failures."
    echo "Review the output above."
fi

echo
echo "=== PCS Base Setup Complete ==="
echo
echo "Useful client access addresses from behind the PCS access point:"
echo
echo "PCS public homepage:"
echo "  http://10.42.0.1/"
echo
echo "PCS Admin Login:"
echo "  http://10.42.0.1/admin/"
echo
echo "Primary file share:"
echo "  \\\\10.42.0.1\\PCS-Share"
echo
echo "Backup file share:"
echo "  \\\\10.42.0.1\\PCS-Backup"
echo
echo "Cockpit:"
echo "  https://10.42.0.1:9090"
echo
echo "LAN NTP:"
echo "  10.42.0.1"
echo
echo "Windows NTP test:"
echo "  w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly"
echo
echo "Recommended reboot validation:"
echo "  sudo reboot"
echo "  cd ${REPO_DIR}"
echo "  ./scripts/pcs-self-test.sh"
echo "  ./scripts/pcs-status.sh"
echo
