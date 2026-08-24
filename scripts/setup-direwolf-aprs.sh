#!/usr/bin/env bash

set -Eeuo pipefail

# Non-interactive SSH and systemd-adjacent shells can omit sbin directories.
# Use a deterministic system path so installed administrative tools are found.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="${REPO_DIR}/scripts"
INSTALL_CONFIG="${PCS_INSTALL_CONFIG:-${REPO_DIR}/config/pcs-install.conf}"
TEMPLATE_SRC="${REPO_DIR}/config/direwolf.example.conf"
COMMISSIONED_PROFILE_SRC="${REPO_DIR}/config/pcs-install.example.conf"
CONTROL_PANEL_SETUP="${SCRIPT_DIR}/setup-pcs-control-panel.sh"
APRS_CONFIG_DIR="/etc/pcs/aprs"
TEMPLATE_DST="${APRS_CONFIG_DIR}/direwolf.example.conf"
DIREWOLF_CONFIG="/etc/direwolf.conf"
BACKUP_DIR="${APRS_CONFIG_DIR}/backups"
LOG_DIR="/var/log/direwolf"
KISS_FIREWALL_SRC="${SCRIPT_DIR}/pcs-aprs-kiss-firewall.sh"
KISS_FIREWALL_DST="/usr/local/sbin/pcs-aprs-kiss-firewall"
KISS_FIREWALL_SERVICE_SRC="${REPO_DIR}/systemd/pcs-aprs-kiss-firewall.service"
KISS_FIREWALL_SERVICE_DST="/etc/systemd/system/pcs-aprs-kiss-firewall.service"
DIREWOLF_OVERRIDE_SRC="${REPO_DIR}/systemd/pcs-direwolf-override.conf"
DIREWOLF_OVERRIDE_DST="/etc/systemd/system/direwolf.service.d/pcs.conf"
SA818_SRC="${SCRIPT_DIR}/pcs_sa818.py"
SA818_DST="/usr/local/sbin/pcs-sa818"
SA818_SERVICE_SRC="${REPO_DIR}/systemd/pcs-sa818.service"
SA818_SERVICE_DST="/etc/systemd/system/pcs-sa818.service"
APRS_AUDIO_SRC="${SCRIPT_DIR}/pcs-aprs-audio.sh"
APRS_AUDIO_DST="/usr/local/sbin/pcs-aprs-audio"
APRS_AUDIO_SERVICE_SRC="${REPO_DIR}/systemd/pcs-aprs-audio.service"
APRS_AUDIO_SERVICE_DST="/etc/systemd/system/pcs-aprs-audio.service"
SOFTWARE_TEST="${SCRIPT_DIR}/test-direwolf-aprs-software.sh"
DIREWOLF_MIN_VERSION="1.8"
DIREWOLF_SOURCE_VERSION="1.8.1"
DIREWOLF_SOURCE_COMMIT="a231971a652bfb574a4bae9a5d875fbce53d2267"
DIREWOLF_SOURCE_URL="https://github.com/wb2osz/direwolf.git"
APRS_CONFIG_VERSION_CURRENT="2"
MODE="${1:---prepare}"
PROFILE="${2:-}"
VALUE2="${3:-}"
APRS_IS_PASSCODE=""

if [[ -f "${INSTALL_CONFIG}" ]]; then
    # shellcheck source=/dev/null
    source "${INSTALL_CONFIG}"
fi

PCS_SETUP_APRS="${PCS_SETUP_APRS:-no}"
PCS_APRS_CONFIG_VERSION="${PCS_APRS_CONFIG_VERSION:-0}"
PCS_APRS_ACTIVE_MODE="${PCS_APRS_ACTIVE_MODE:-staged}"
PCS_APRS_ROLE="${PCS_APRS_ROLE:-digi-igate}"
PCS_APRS_CALLSIGN="${PCS_APRS_CALLSIGN:-W8IJC-10}"
PCS_APRS_FREQUENCY="${PCS_APRS_FREQUENCY:-144.550 MHz}"
PCS_APRS_RADIO="${PCS_APRS_RADIO:-SA818S / EasyDigi / Sabrent USB audio}"
PCS_APRS_AUDIO_INPUT="${PCS_APRS_AUDIO_INPUT:-plughw:CARD=Device,DEV=0}"
PCS_APRS_AUDIO_OUTPUT="${PCS_APRS_AUDIO_OUTPUT:-plughw:CARD=Device,DEV=0}"
PCS_APRS_AUDIO_CARD="${PCS_APRS_AUDIO_CARD:-Device}"
PCS_APRS_PLAYBACK_CONTROL="${PCS_APRS_PLAYBACK_CONTROL:-Speaker}"
PCS_APRS_PLAYBACK_LEVEL="${PCS_APRS_PLAYBACK_LEVEL:--18dB}"
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
PCS_APRS_LOG_RETENTION_DAYS="${PCS_APRS_LOG_RETENTION_DAYS:-14}"
PCS_APRS_DWAIT="${PCS_APRS_DWAIT:-0}"
PCS_APRS_SLOTTIME="${PCS_APRS_SLOTTIME:-10}"
PCS_APRS_PERSIST="${PCS_APRS_PERSIST:-63}"
PCS_APRS_TXDELAY="${PCS_APRS_TXDELAY:-60}"
PCS_APRS_TXTAIL="${PCS_APRS_TXTAIL:-20}"
PCS_APRS_FULLDUP="${PCS_APRS_FULLDUP:-OFF}"
PCS_APRS_RX_AUDIO_VALIDATED="${PCS_APRS_RX_AUDIO_VALIDATED:-no}"
PCS_APRS_RADIO_CHANNEL_VALIDATED="${PCS_APRS_RADIO_CHANNEL_VALIDATED:-no}"
PCS_APRS_PTT_VALIDATED="${PCS_APRS_PTT_VALIDATED:-no}"
PCS_APRS_TX_AUDIO_VALIDATED="${PCS_APRS_TX_AUDIO_VALIDATED:-no}"
PCS_APRS_TX_TIMING_VALIDATED="${PCS_APRS_TX_TIMING_VALIDATED:-no}"
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

usage() {
    cat <<'EOF'
Usage: ./scripts/setup-direwolf-aprs.sh COMMAND [ARGUMENTS]

  --prepare              Install Dire Wolf and stage the safe template.
                         The service remains stopped and disabled.
  --configure-options    Record the non-secret desired APRS profile. Does not
                         collect an APRS-IS passcode or activate RF.
  --import-commissioned-profile
                         Atomically replace all desired APRS settings with the
                         versioned PCS profile and reset hardware evidence.
  --prepare-uart         Idempotently enable the Pi UART, remove the serial
                         login console, and report whether a reboot is needed.
  --record-validation    Interactively record completed hardware evidence gates.
  --check                Report package, service, configuration, and audio state.
  --capabilities         Report Dire Wolf version and required feature support.
  --list-audio           Perform read-only USB, ALSA, and PTT discovery.
  --detect-audio         Record exactly one unambiguous USB ALSA capture/playback
                         card by stable card ID; refuse zero or multiple matches.
  --set-rx-level PERCENT Persist and apply the USB capture level without
                         regenerating Dire Wolf or touching the TX profile.
  --set-tx-timing DELAY TAIL
                         Record validated 10 ms TXDELAY/TXTAIL units. Dire Wolf
                         must be active and values must match its live file.
  --software-test        Run AX.25, FX.25, and timing loopback fixtures.
  --render-config PROFILE
                         Print a proposed rx or tx configuration. Secrets are
                         replaced by a visible placeholder; nothing is changed.
  --validate-config PROFILE
                         Lint a proposed rx or tx configuration and report all
                         activation blockers without changing the system.
  --activate-rx          Transactionally install a receive/IGate configuration.
                         Playback, PTT, beaconing, digipeating, FX.25 TX, and
                         Internet-to-RF message gating remain disabled.
  --activate-tx          Transactionally install the complete transmit profile.
                         Refuses unless every hardware validation is recorded.
  --rollback             Restore the newest PCS Dire Wolf configuration backup.

PROFILE must be rx or tx. Transmit activation requires explicit validation of
the radio channel, receive audio, PTT polarity, transmit audio/deviation, and
Dire Wolf timing. The typed RF confirmation is still required after commissioning.
EOF
}

source_commissioned_profile() {
    if [[ ! -f "${COMMISSIONED_PROFILE_SRC}" ]]; then
        echo "ERROR: commissioned APRS profile is missing: ${COMMISSIONED_PROFILE_SRC}" >&2
        return 1
    fi

    # This is a version-controlled shell config owned by the same repository as
    # this installer. Operational state is deliberately not imported.
    # shellcheck source=/dev/null
    source <(grep -E '^PCS_APRS_[A-Z0-9_]+=' "${COMMISSIONED_PROFILE_SRC}" \
        | grep -Ev '^PCS_APRS_ACTIVE_MODE=')
}

replace_with_commissioned_profile() {
    local config_dir
    local temp_file

    require_normal_user
    if [[ ! -f "${COMMISSIONED_PROFILE_SRC}" ]]; then
        echo "ERROR: commissioned APRS profile is missing: ${COMMISSIONED_PROFILE_SRC}" >&2
        return 1
    fi

    config_dir="$(dirname "${INSTALL_CONFIG}")"
    mkdir -p "${config_dir}"
    temp_file="$(mktemp "${config_dir}/.pcs-install.XXXXXX")"

    if [[ -f "${INSTALL_CONFIG}" ]]; then
        cp -p -- "${INSTALL_CONFIG}" "${INSTALL_CONFIG}.bak"
        awk '
            /^[[:space:]]*PCS_APRS_[A-Z0-9_]+=/ {
                if ($0 ~ /^[[:space:]]*PCS_APRS_ACTIVE_MODE=/) print
                next
            }
            { print }
        ' "${INSTALL_CONFIG}" >"${temp_file}"
    else
        {
            echo "# PCS install config"
            echo "# Generated by scripts/setup-direwolf-aprs.sh"
        } >"${temp_file}"
    fi

    {
        echo
        echo "# Managed commissioned APRS profile; hardware evidence must be recorded again."
        grep -E '^PCS_APRS_[A-Z0-9_]+=' "${COMMISSIONED_PROFILE_SRC}" \
            | grep -Ev '^PCS_APRS_ACTIVE_MODE='
    } >>"${temp_file}"

    chmod 0600 "${temp_file}"
    mv -f -- "${temp_file}" "${INSTALL_CONFIG}"

    echo "Imported the versioned commissioned APRS profile into ${INSTALL_CONFIG}."
    if [[ -f "${INSTALL_CONFIG}.bak" ]]; then
        echo "Previous local configuration backup: ${INSTALL_CONFIG}.bak"
    fi
    echo "All APRS hardware validation gates are no. No service or RF state was changed."
}

ask_choice() {
    local prompt="$1"
    local default_value="$2"
    shift 2
    local choices=("$@")
    local answer
    local choice

    while true; do
        read -r -p "${prompt} [${choices[*]}] (default: ${default_value}): " answer
        answer="${answer:-${default_value}}"
        for choice in "${choices[@]}"; do
            if [[ "${answer}" == "${choice}" ]]; then
                echo "${answer}"
                return 0
            fi
        done
        echo "Choose one of: ${choices[*]}" >&2
    done
}

ask_safe_text() {
    local prompt="$1"
    local default_value="$2"
    local pattern="$3"
    local allow_empty="${4:-no}"
    local answer

    while true; do
        read -r -p "${prompt} [${default_value}]: " answer
        answer="${answer:-${default_value}}"
        if [[ -z "${answer}" && "${allow_empty}" == "yes" ]]; then
            echo ""
            return 0
        fi
        if [[ "${answer}" =~ ${pattern} ]]; then
            echo "${answer}"
            return 0
        fi
        echo "Value contains unsupported characters or is outside the allowed format." >&2
    done
}

ask_port() {
    local prompt="$1"
    local default_value="$2"
    local answer

    while true; do
        read -r -p "${prompt} [${default_value}; 0 disables]: " answer
        answer="${answer:-${default_value}}"
        if [[ "${answer}" =~ ^[0-9]+$ ]] \
            && (( answer == 0 || (answer >= 1024 && answer <= 65535) )); then
            echo "${answer}"
            return 0
        fi
        echo "Use 0 to disable or an unprivileged TCP port from 1024 through 65535." >&2
    done
}

configure_options() {
    local desired_tx="no"

    require_normal_user
    if [[ ! -t 0 ]]; then
        echo "ERROR: --configure-options requires an interactive terminal."
        exit 1
    fi

    if [[ "${PCS_APRS_CONFIG_VERSION}" != "${APRS_CONFIG_VERSION_CURRENT}" ]]; then
        echo "Migrating legacy APRS defaults to managed profile version ${APRS_CONFIG_VERSION_CURRENT}."
        echo "Unprompted legacy values and hardware evidence will be reset before review."
        source_commissioned_profile
    fi

    echo
    echo "=== Configure Desired PCS APRS Profile ==="
    echo
    echo "This records non-secret desired settings only. It does not generate"
    echo "/etc/direwolf.conf, enable the service, open ports, or transmit."
    echo

    PCS_APRS_ROLE="$(ask_choice "Operating role" "${PCS_APRS_ROLE}" monitor rx-igate digipeater tracker digi-igate)"
    PCS_APRS_CALLSIGN="$(ask_safe_text "Station callsign/SSID (blank allowed while staging)" "${PCS_APRS_CALLSIGN}" '^[A-Za-z0-9]{1,6}(-([1-9]|1[0-5]))?$' yes)"
    PCS_APRS_CALLSIGN="${PCS_APRS_CALLSIGN^^}"
    PCS_APRS_FREQUENCY="$(ask_safe_text "Operator-facing frequency/band-plan label" "${PCS_APRS_FREQUENCY}" '^[A-Za-z0-9._/+ -]{1,40}$')"
    PCS_APRS_MODEM="$(ask_choice "Dire Wolf modem" "${PCS_APRS_MODEM}" 300 1200 9600)"
    PCS_APRS_SAMPLE_RATE="$(ask_choice "Audio sample rate" "${PCS_APRS_SAMPLE_RATE}" 44100 48000 96000)"
    PCS_APRS_AUDIO_CHANNELS="$(ask_choice "Audio channels" "${PCS_APRS_AUDIO_CHANNELS}" 1 2)"
    PCS_APRS_AUDIO_INPUT="$(ask_safe_text "ALSA capture device (auto until detected)" "${PCS_APRS_AUDIO_INPUT}" '^(auto|[A-Za-z0-9_.,:/=+-]+)$')"
    PCS_APRS_AGW_PORT="$(ask_port "AGW TCP port" "${PCS_APRS_AGW_PORT}")"
    PCS_APRS_KISS_PORT="$(ask_port "KISS TCP port" "${PCS_APRS_KISS_PORT}")"

    case "${PCS_APRS_ROLE}" in
        rx-igate|digi-igate)
            PCS_APRS_IGATE="yes"
            ;;
        *)
            PCS_APRS_IGATE="$(ask_choice "Enable APRS-IS in the desired profile" "${PCS_APRS_IGATE}" yes no)"
            ;;
    esac

    if [[ "${PCS_APRS_IGATE}" == "yes" ]]; then
        PCS_APRS_IGATE_SERVER="$(ask_safe_text "APRS-IS server" "${PCS_APRS_IGATE_SERVER}" '^[A-Za-z0-9.-]+$')"
        echo "APRS-IS passcode is not collected here and will never be stored in pcs-install.conf."
    fi

    PCS_APRS_GPSD="$(ask_choice "Use GPSD in the desired profile" "${PCS_APRS_GPSD}" yes no)"

    case "${PCS_APRS_ROLE}" in
        digipeater|tracker|digi-igate)
            desired_tx="$(ask_choice "Record this as a transmit-capable desired profile" "${PCS_APRS_TX_ENABLED}" yes no)"
            ;;
        *)
            desired_tx="no"
            ;;
    esac
    PCS_APRS_TX_ENABLED="${desired_tx}"

    if [[ "${PCS_APRS_TX_ENABLED}" == "yes" ]]; then
        PCS_APRS_AUDIO_OUTPUT="$(ask_safe_text "ALSA playback device (auto until detected)" "${PCS_APRS_AUDIO_OUTPUT}" '^(auto|null|[A-Za-z0-9_.,:/=+-]+)$')"
        PCS_APRS_PTT_METHOD="$(ask_choice "Planned PTT method" "${PCS_APRS_PTT_METHOD}" none cm108 serial-rts serial-dtr gpio hamlib vox)"
        PCS_APRS_BEACON="$(ask_choice "Enable beaconing in the desired profile" "${PCS_APRS_BEACON}" yes no)"
        if [[ "${PCS_APRS_BEACON}" == "yes" ]]; then
            PCS_APRS_BEACON_PATH="$(ask_safe_text "Beacon RF path (direct or APRS path)" "${PCS_APRS_BEACON_PATH}" '^(direct|not selected|[A-Z0-9,-]{1,40})$')"
            PCS_APRS_BEACON_SYMBOL="$(ask_safe_text "Dire Wolf beacon symbol name" "${PCS_APRS_BEACON_SYMBOL}" '^(not selected|[A-Za-z0-9 _/-]{1,40})$')"
            PCS_APRS_BEACON_COMMENT="$(ask_safe_text "Public beacon comment" "${PCS_APRS_BEACON_COMMENT}" '^[A-Za-z0-9 .,_/+:-]{1,80}$')"
        fi
        PCS_APRS_DIGIPEAT="$(ask_choice "Enable digipeating in the desired profile" "${PCS_APRS_DIGIPEAT}" yes no)"
        PCS_APRS_FX25_TX="$(ask_choice "Enable FX.25 transmit in the desired profile" "${PCS_APRS_FX25_TX}" yes no)"
    else
        PCS_APRS_AUDIO_OUTPUT="null"
        PCS_APRS_PTT_METHOD="none"
        PCS_APRS_BEACON="no"
        PCS_APRS_DIGIPEAT="no"
        PCS_APRS_FX25_TX="no"
    fi

    PCS_APRS_LOGGING="$(ask_choice "Enable Dire Wolf packet logging in the desired profile" "${PCS_APRS_LOGGING}" yes no)"
    PCS_APRS_LOG_RETENTION_DAYS="$(ask_safe_text "Packet log retention in days" "${PCS_APRS_LOG_RETENTION_DAYS}" '^[1-9][0-9]{0,2}$')"

    set_install_config_value PCS_APRS_ROLE "${PCS_APRS_ROLE}"
    set_install_config_value PCS_APRS_CALLSIGN "${PCS_APRS_CALLSIGN}"
    set_install_config_value PCS_APRS_FREQUENCY "${PCS_APRS_FREQUENCY}"
    set_install_config_value PCS_APRS_RADIO "${PCS_APRS_RADIO}"
    set_install_config_value PCS_APRS_AUDIO_INPUT "${PCS_APRS_AUDIO_INPUT}"
    set_install_config_value PCS_APRS_AUDIO_OUTPUT "${PCS_APRS_AUDIO_OUTPUT}"
    set_install_config_value PCS_APRS_AUDIO_CARD "${PCS_APRS_AUDIO_CARD}"
    set_install_config_value PCS_APRS_PLAYBACK_CONTROL "${PCS_APRS_PLAYBACK_CONTROL}"
    set_install_config_value PCS_APRS_PLAYBACK_LEVEL "${PCS_APRS_PLAYBACK_LEVEL}"
    set_install_config_value PCS_APRS_CAPTURE_CONTROL "${PCS_APRS_CAPTURE_CONTROL}"
    set_install_config_value PCS_APRS_CAPTURE_LEVEL "${PCS_APRS_CAPTURE_LEVEL}"
    set_install_config_value PCS_APRS_AGC_CONTROL "${PCS_APRS_AGC_CONTROL}"
    set_install_config_value PCS_APRS_AGC_STATE "${PCS_APRS_AGC_STATE}"
    set_install_config_value PCS_APRS_SAMPLE_RATE "${PCS_APRS_SAMPLE_RATE}"
    set_install_config_value PCS_APRS_AUDIO_CHANNELS "${PCS_APRS_AUDIO_CHANNELS}"
    set_install_config_value PCS_APRS_MODEM "${PCS_APRS_MODEM}"
    set_install_config_value PCS_APRS_PTT_METHOD "${PCS_APRS_PTT_METHOD}"
    set_install_config_value PCS_APRS_PTT_INTERFACE "${PCS_APRS_PTT_INTERFACE}"
    set_install_config_value PCS_APRS_PTT_GPIO_LINE "${PCS_APRS_PTT_GPIO_LINE}"
    set_install_config_value PCS_APRS_PTT_ACTIVE_LEVEL "${PCS_APRS_PTT_ACTIVE_LEVEL}"
    set_install_config_value PCS_APRS_AGW_PORT "${PCS_APRS_AGW_PORT}"
    set_install_config_value PCS_APRS_KISS_PORT "${PCS_APRS_KISS_PORT}"
    set_install_config_value PCS_APRS_KISS_LAN_INTERFACE "${PCS_APRS_KISS_LAN_INTERFACE}"
    set_install_config_value PCS_APRS_KISS_LAN_NETWORK "${PCS_APRS_KISS_LAN_NETWORK}"
    set_install_config_value PCS_APRS_IGATE "${PCS_APRS_IGATE}"
    set_install_config_value PCS_APRS_IGATE_SERVER "${PCS_APRS_IGATE_SERVER}"
    set_install_config_value PCS_APRS_IGATE_MODE "${PCS_APRS_IGATE_MODE}"
    set_install_config_value PCS_APRS_IGATE_RF_TO_IS_FILTER "${PCS_APRS_IGATE_RF_TO_IS_FILTER}"
    set_install_config_value PCS_APRS_IGATE_IS_TO_RF_FILTER "${PCS_APRS_IGATE_IS_TO_RF_FILTER}"
    set_install_config_value PCS_APRS_IGATE_TX_PATH "${PCS_APRS_IGATE_TX_PATH}"
    set_install_config_value PCS_APRS_IGATE_TX_LIMIT_1M "${PCS_APRS_IGATE_TX_LIMIT_1M}"
    set_install_config_value PCS_APRS_IGATE_TX_LIMIT_5M "${PCS_APRS_IGATE_TX_LIMIT_5M}"
    set_install_config_value PCS_APRS_GPSD "${PCS_APRS_GPSD}"
    set_install_config_value PCS_APRS_GPSD_HOST "${PCS_APRS_GPSD_HOST}"
    set_install_config_value PCS_APRS_GPSD_PORT "${PCS_APRS_GPSD_PORT}"
    set_install_config_value PCS_APRS_BEACON "${PCS_APRS_BEACON}"
    set_install_config_value PCS_APRS_BEACON_TYPE "${PCS_APRS_BEACON_TYPE}"
    set_install_config_value PCS_APRS_BEACON_INTERVAL "${PCS_APRS_BEACON_INTERVAL}"
    set_install_config_value PCS_APRS_BEACON_PATH "${PCS_APRS_BEACON_PATH}"
    set_install_config_value PCS_APRS_BEACON_SENDTO "${PCS_APRS_BEACON_SENDTO}"
    set_install_config_value PCS_APRS_BEACON_SYMBOL "${PCS_APRS_BEACON_SYMBOL}"
    set_install_config_value PCS_APRS_BEACON_OVERLAY "${PCS_APRS_BEACON_OVERLAY}"
    set_install_config_value PCS_APRS_BEACON_ALTITUDE "${PCS_APRS_BEACON_ALTITUDE}"
    set_install_config_value PCS_APRS_BEACON_COMMENT "${PCS_APRS_BEACON_COMMENT}"
    set_install_config_value PCS_APRS_DIGIPEAT "${PCS_APRS_DIGIPEAT}"
    set_install_config_value PCS_APRS_DIGIPEAT_MODE "${PCS_APRS_DIGIPEAT_MODE}"
    set_install_config_value PCS_APRS_DIGIPEAT_ALIAS "${PCS_APRS_DIGIPEAT_ALIAS}"
    set_install_config_value PCS_APRS_DIGIPEAT_ALIAS_PATTERN "${PCS_APRS_DIGIPEAT_ALIAS_PATTERN}"
    set_install_config_value PCS_APRS_DIGIPEAT_WIDE_PATTERN "${PCS_APRS_DIGIPEAT_WIDE_PATTERN}"
    set_install_config_value PCS_APRS_DIGIPEAT_PREEMPTIVE "${PCS_APRS_DIGIPEAT_PREEMPTIVE}"
    set_install_config_value PCS_APRS_DIGIPEAT_FILTER "${PCS_APRS_DIGIPEAT_FILTER}"
    set_install_config_value PCS_APRS_DIGIPEAT_DEDUPE_SECONDS "${PCS_APRS_DIGIPEAT_DEDUPE_SECONDS}"
    set_install_config_value PCS_APRS_TX_ENABLED "${PCS_APRS_TX_ENABLED}"
    set_install_config_value PCS_APRS_FX25_TX "${PCS_APRS_FX25_TX}"
    set_install_config_value PCS_APRS_LOGGING "${PCS_APRS_LOGGING}"
    set_install_config_value PCS_APRS_LOG_RETENTION_DAYS "${PCS_APRS_LOG_RETENTION_DAYS}"
    set_install_config_value PCS_APRS_DWAIT "${PCS_APRS_DWAIT}"
    set_install_config_value PCS_APRS_SLOTTIME "${PCS_APRS_SLOTTIME}"
    set_install_config_value PCS_APRS_PERSIST "${PCS_APRS_PERSIST}"
    set_install_config_value PCS_APRS_TXDELAY "${PCS_APRS_TXDELAY}"
    set_install_config_value PCS_APRS_TXTAIL "${PCS_APRS_TXTAIL}"
    set_install_config_value PCS_APRS_FULLDUP "${PCS_APRS_FULLDUP}"
    set_install_config_value PCS_APRS_RX_AUDIO_VALIDATED "${PCS_APRS_RX_AUDIO_VALIDATED}"
    set_install_config_value PCS_APRS_RADIO_CHANNEL_VALIDATED "${PCS_APRS_RADIO_CHANNEL_VALIDATED}"
    set_install_config_value PCS_APRS_PTT_VALIDATED "${PCS_APRS_PTT_VALIDATED}"
    set_install_config_value PCS_APRS_TX_AUDIO_VALIDATED "${PCS_APRS_TX_AUDIO_VALIDATED}"
    set_install_config_value PCS_APRS_TX_TIMING_VALIDATED "${PCS_APRS_TX_TIMING_VALIDATED}"
    set_install_config_value PCS_APRS_RADIO_INIT "${PCS_APRS_RADIO_INIT}"
    set_install_config_value PCS_APRS_RADIO_DEVICE "${PCS_APRS_RADIO_DEVICE}"
    set_install_config_value PCS_APRS_RADIO_BAUD "${PCS_APRS_RADIO_BAUD}"
    set_install_config_value PCS_APRS_RADIO_BANDWIDTH_KHZ "${PCS_APRS_RADIO_BANDWIDTH_KHZ}"
    set_install_config_value PCS_APRS_RADIO_TX_FREQUENCY_MHZ "${PCS_APRS_RADIO_TX_FREQUENCY_MHZ}"
    set_install_config_value PCS_APRS_RADIO_RX_FREQUENCY_MHZ "${PCS_APRS_RADIO_RX_FREQUENCY_MHZ}"
    set_install_config_value PCS_APRS_RADIO_TX_TONE "${PCS_APRS_RADIO_TX_TONE}"
    set_install_config_value PCS_APRS_RADIO_RX_TONE "${PCS_APRS_RADIO_RX_TONE}"
    set_install_config_value PCS_APRS_RADIO_SQUELCH "${PCS_APRS_RADIO_SQUELCH}"
    set_install_config_value PCS_APRS_RADIO_VOLUME "${PCS_APRS_RADIO_VOLUME}"
    set_install_config_value PCS_APRS_RADIO_PRE_DE_EMPHASIS "${PCS_APRS_RADIO_PRE_DE_EMPHASIS}"
    set_install_config_value PCS_APRS_RADIO_HIGH_PASS "${PCS_APRS_RADIO_HIGH_PASS}"
    set_install_config_value PCS_APRS_RADIO_LOW_PASS "${PCS_APRS_RADIO_LOW_PASS}"
    set_install_config_value PCS_APRS_RADIO_TX_TAIL "${PCS_APRS_RADIO_TX_TAIL}"
    set_install_config_value PCS_APRS_CONFIG_VERSION "${APRS_CONFIG_VERSION_CURRENT}"
    prune_unknown_aprs_config_keys

    echo
    echo "Desired APRS options saved to ${INSTALL_CONFIG}."
    echo "No live Dire Wolf service or RF state was changed. Run --list-audio after the"
    echo "sound card and radio/PTT path are present, then review docs/direwolf-aprs.md before activation."
}

record_validation() {
    require_normal_user
    if [[ ! -t 0 ]]; then
        echo "ERROR: --record-validation requires an interactive terminal."
        exit 1
    fi

    echo
    echo "=== Record PCS APRS Hardware Validation ==="
    echo
    echo "Answer yes only for checks that were actually completed and recorded."
    echo "This command does not activate Dire Wolf or transmit."
    echo
    PCS_APRS_RX_AUDIO_VALIDATED="$(ask_choice "Receive audio decodes known packets reliably" "${PCS_APRS_RX_AUDIO_VALIDATED}" yes no)"
    PCS_APRS_RADIO_CHANNEL_VALIDATED="$(ask_choice "Radio is programmed and independently checked for ${PCS_APRS_FREQUENCY}" "${PCS_APRS_RADIO_CHANNEL_VALIDATED}" yes no)"
    PCS_APRS_PTT_VALIDATED="$(ask_choice "GPIO${PCS_APRS_PTT_GPIO_LINE}/EasyDigi PTT passed the disconnected-radio polarity test" "${PCS_APRS_PTT_VALIDATED}" yes no)"
    PCS_APRS_TX_AUDIO_VALIDATED="$(ask_choice "Transmit audio and deviation were measured into a dummy load" "${PCS_APRS_TX_AUDIO_VALIDATED}" yes no)"
    PCS_APRS_TX_TIMING_VALIDATED="$(ask_choice "Channel-access and PTT timing values were measured" "${PCS_APRS_TX_TIMING_VALIDATED}" yes no)"

    set_install_config_value PCS_APRS_RX_AUDIO_VALIDATED "${PCS_APRS_RX_AUDIO_VALIDATED}"
    set_install_config_value PCS_APRS_RADIO_CHANNEL_VALIDATED "${PCS_APRS_RADIO_CHANNEL_VALIDATED}"
    set_install_config_value PCS_APRS_PTT_VALIDATED "${PCS_APRS_PTT_VALIDATED}"
    set_install_config_value PCS_APRS_TX_AUDIO_VALIDATED "${PCS_APRS_TX_AUDIO_VALIDATED}"
    set_install_config_value PCS_APRS_TX_TIMING_VALIDATED "${PCS_APRS_TX_TIMING_VALIDATED}"
    echo "Validation evidence state saved. No service or RF state was changed."
}

list_audio() {
    local card_id

    echo
    echo "=== PCS APRS Audio / PTT Discovery ==="
    echo

    echo "--- USB devices ---"
    if command -v lsusb >/dev/null 2>&1; then
        lsusb || true
    else
        echo "lsusb is not installed."
    fi
    echo

    echo "--- ALSA capture hardware ---"
    if command -v arecord >/dev/null 2>&1; then
        arecord -l 2>&1 || true
    else
        echo "arecord is not installed."
    fi
    echo

    echo "--- ALSA playback hardware ---"
    if command -v aplay >/dev/null 2>&1; then
        aplay -l 2>&1 || true
    else
        echo "aplay is not installed."
    fi
    echo

    echo "--- Stable ALSA card-ID candidates ---"
    if [[ -r /proc/asound/cards ]]; then
        while IFS= read -r card_id; do
            [[ -n "${card_id}" ]] || continue
            echo "plughw:CARD=${card_id},DEV=0"
        done < <(awk '/^[[:space:]]*[0-9]+[[:space:]]+\[/ { id=$2; gsub(/[\[\]]/, "", id); print id }' /proc/asound/cards)
    else
        echo "/proc/asound/cards is unavailable."
    fi
    echo

    echo "--- Dire Wolf CM108/CM119 discovery ---"
    if command -v cm108 >/dev/null 2>&1; then
        cm108 || true
    else
        echo "cm108 is unavailable until a compatible Dire Wolf package is installed."
    fi
    echo

    echo "Discovery is read-only. Confirm the capture/playback pair and PTT method"
    echo "with the actual interface before generating or enabling /etc/direwolf.conf."
}

detect_audio() {
    local card_dir=""
    local card_index=""
    local card_id=""
    local device_path=""
    local stable_device=""
    local -a capture_nodes=()
    local -a playback_nodes=()
    local -a candidates=()

    require_normal_user
    if [[ ! -d /proc/asound ]]; then
        echo "ERROR: ALSA card information is unavailable under /proc/asound." >&2
        return 1
    fi

    shopt -s nullglob
    for card_dir in /proc/asound/card[0-9]*; do
        [[ -d "${card_dir}" ]] || continue
        card_index="${card_dir##*card}"
        [[ "${card_index}" =~ ^[0-9]+$ ]] || continue
        capture_nodes=("${card_dir}"/pcm*c)
        playback_nodes=("${card_dir}"/pcm*p)
        (( ${#capture_nodes[@]} > 0 && ${#playback_nodes[@]} > 0 )) || continue
        device_path="$(readlink -f "/sys/class/sound/card${card_index}/device" 2>/dev/null || true)"
        [[ "${device_path}" == *"/usb"* || "${device_path}" == *"/usb/"* ]] || continue
        card_id="$(tr -d '[:space:]' <"${card_dir}/id" 2>/dev/null || true)"
        [[ "${card_id}" =~ ^[A-Za-z0-9_+-]+$ ]] || continue
        candidates+=("${card_id}")
    done
    shopt -u nullglob

    if (( ${#candidates[@]} == 0 )); then
        echo "ERROR: no USB ALSA card with both capture and playback was found." >&2
        echo "Run --list-audio and inspect the hardware connection." >&2
        return 1
    fi
    if (( ${#candidates[@]} > 1 )); then
        echo "ERROR: multiple USB ALSA capture/playback cards were found; PCS will not guess:" >&2
        printf '  - plughw:CARD=%s,DEV=0\n' "${candidates[@]}" >&2
        echo "Set PCS_APRS_AUDIO_INPUT and PCS_APRS_AUDIO_OUTPUT explicitly." >&2
        return 1
    fi

    stable_device="plughw:CARD=${candidates[0]},DEV=0"
    set_install_config_value PCS_APRS_AUDIO_INPUT "${stable_device}"
    set_install_config_value PCS_APRS_AUDIO_OUTPUT "${stable_device}"
    set_install_config_value PCS_APRS_RX_AUDIO_VALIDATED no
    set_install_config_value PCS_APRS_TX_AUDIO_VALIDATED no
    PCS_APRS_AUDIO_INPUT="${stable_device}"
    PCS_APRS_AUDIO_OUTPUT="${stable_device}"
    PCS_APRS_RX_AUDIO_VALIDATED="no"
    PCS_APRS_TX_AUDIO_VALIDATED="no"
    echo "Recorded unambiguous USB ALSA device: ${stable_device}"
    echo "Audio validation gates were reset to no; detection is not level/deviation validation."
}

require_normal_user() {
    if [[ "${EUID}" -eq 0 ]]; then
        echo "ERROR: Do not run this script with sudo."
        echo "Run it as the normal Pi user; it requests sudo only where needed."
        exit 1
    fi
}

set_install_config_value() {
    local key="$1"
    local value="$2"
    local config_dir
    local temp_file

    config_dir="$(dirname "${INSTALL_CONFIG}")"
    mkdir -p "${config_dir}"
    temp_file="$(mktemp "${config_dir}/.pcs-install.XXXXXX")"

    if [[ -f "${INSTALL_CONFIG}" ]]; then
        awk -v key="${key}" -v value="${value}" '
            BEGIN { replaced=0 }
            $0 ~ "^[[:space:]]*" key "=" {
                printf "%s=%c%s%c\n", key, 34, value, 34
                replaced=1
                next
            }
            { print }
            END {
                if (!replaced) {
                    printf "%s=%c%s%c\n", key, 34, value, 34
                }
            }
        ' "${INSTALL_CONFIG}" >"${temp_file}"
    else
        {
            echo "# PCS install config"
            echo "# Generated by scripts/setup-direwolf-aprs.sh"
            printf '%s="%s"\n' "${key}" "${value}"
        } >"${temp_file}"
    fi

    chmod 0600 "${temp_file}"
    mv -f -- "${temp_file}" "${INSTALL_CONFIG}"
}

prune_unknown_aprs_config_keys() {
    local allowed_file
    local config_dir
    local temp_file

    [[ -f "${INSTALL_CONFIG}" && -f "${COMMISSIONED_PROFILE_SRC}" ]] || return 0
    config_dir="$(dirname "${INSTALL_CONFIG}")"
    allowed_file="$(mktemp)"
    temp_file="$(mktemp "${config_dir}/.pcs-install.XXXXXX")"
    grep -Eo '^PCS_APRS_[A-Z0-9_]+' "${COMMISSIONED_PROFILE_SRC}" >"${allowed_file}"
    awk '
        NR == FNR { allowed[$1]=1; next }
        /^[[:space:]]*PCS_APRS_[A-Z0-9_]+=/ {
            key=$0
            sub(/^[[:space:]]*/, "", key)
            sub(/=.*/, "", key)
            if (!(key in allowed)) next
        }
        { print }
    ' "${allowed_file}" "${INSTALL_CONFIG}" >"${temp_file}"
    rm -f -- "${allowed_file}"
    chmod 0600 "${temp_file}"
    mv -f -- "${temp_file}" "${INSTALL_CONFIG}"
}

record_blocker() {
    ACTIVATION_BLOCKERS+=("$1")
}

validate_profile_values() {
    local failed=0

    validate_value() {
        local name="$1"
        local value="$2"
        local pattern="$3"
        if [[ ! "${value}" =~ ${pattern} ]]; then
            echo "ERROR: ${name} has an unsupported value: ${value}" >&2
            failed=1
        fi
    }

    validate_value PCS_APRS_CALLSIGN "${PCS_APRS_CALLSIGN}" '^[A-Z0-9]{1,6}(-([1-9]|1[0-5]))?$'
    validate_value PCS_APRS_ROLE "${PCS_APRS_ROLE}" '^(monitor|rx-igate|digipeater|tracker|digi-igate)$'
    validate_value PCS_APRS_ACTIVE_MODE "${PCS_APRS_ACTIVE_MODE}" '^(staged|rx|tx)$'
    validate_value PCS_APRS_AUDIO_INPUT "${PCS_APRS_AUDIO_INPUT}" '^(auto|[A-Za-z0-9_.,:/=+-]+)$'
    validate_value PCS_APRS_AUDIO_OUTPUT "${PCS_APRS_AUDIO_OUTPUT}" '^(auto|null|[A-Za-z0-9_.,:/=+-]+)$'
    validate_value PCS_APRS_AUDIO_CARD "${PCS_APRS_AUDIO_CARD}" '^[A-Za-z0-9_.-]{1,64}$'
    validate_value PCS_APRS_PLAYBACK_CONTROL "${PCS_APRS_PLAYBACK_CONTROL}" '^[A-Za-z0-9 _./+-]{1,80}$'
    validate_value PCS_APRS_PLAYBACK_LEVEL "${PCS_APRS_PLAYBACK_LEVEL}" '^(-[0-9]{1,3}dB|[0-9]{1,3}%)$'
    validate_value PCS_APRS_CAPTURE_CONTROL "${PCS_APRS_CAPTURE_CONTROL}" '^[A-Za-z0-9 _./+-]{1,80}$'
    validate_value PCS_APRS_CAPTURE_LEVEL "${PCS_APRS_CAPTURE_LEVEL}" '^[0-9]{1,3}%$'
    validate_value PCS_APRS_AGC_CONTROL "${PCS_APRS_AGC_CONTROL}" '^[A-Za-z0-9 _./+-]{1,80}$'
    validate_value PCS_APRS_AGC_STATE "${PCS_APRS_AGC_STATE}" '^(on|off)$'
    validate_value PCS_APRS_SAMPLE_RATE "${PCS_APRS_SAMPLE_RATE}" '^(44100|48000|96000)$'
    validate_value PCS_APRS_AUDIO_CHANNELS "${PCS_APRS_AUDIO_CHANNELS}" '^[12]$'
    validate_value PCS_APRS_MODEM "${PCS_APRS_MODEM}" '^(300|1200|9600)$'
    validate_value PCS_APRS_AGW_PORT "${PCS_APRS_AGW_PORT}" '^[0-9]{1,5}$'
    validate_value PCS_APRS_KISS_PORT "${PCS_APRS_KISS_PORT}" '^[0-9]{1,5}$'
    validate_value PCS_APRS_KISS_LAN_INTERFACE "${PCS_APRS_KISS_LAN_INTERFACE}" '^[A-Za-z0-9_.:-]+$'
    validate_value PCS_APRS_KISS_LAN_NETWORK "${PCS_APRS_KISS_LAN_NETWORK}" '^[0-9.]+/[0-9]{1,2}$'
    validate_value PCS_APRS_IGATE_SERVER "${PCS_APRS_IGATE_SERVER}" '^[A-Za-z0-9.-]+$'
    validate_value PCS_APRS_IGATE_MODE "${PCS_APRS_IGATE_MODE}" '^(rx-only|two-way)$'
    validate_value PCS_APRS_IGATE_RF_TO_IS_FILTER "${PCS_APRS_IGATE_RF_TO_IS_FILTER}" '^[A-Za-z0-9_/*,|&!().:+ -]{1,160}$'
    validate_value PCS_APRS_IGATE_IS_TO_RF_FILTER "${PCS_APRS_IGATE_IS_TO_RF_FILTER}" '^[A-Za-z0-9_/*,|&!().:+ -]{1,160}$'
    validate_value PCS_APRS_IGATE_TX_PATH "${PCS_APRS_IGATE_TX_PATH}" '^(direct|[A-Z0-9,-]{1,40})$'
    validate_value PCS_APRS_IGATE_TX_LIMIT_1M "${PCS_APRS_IGATE_TX_LIMIT_1M}" '^[0-9]{1,3}$'
    validate_value PCS_APRS_IGATE_TX_LIMIT_5M "${PCS_APRS_IGATE_TX_LIMIT_5M}" '^[0-9]{1,3}$'
    validate_value PCS_APRS_GPSD_HOST "${PCS_APRS_GPSD_HOST}" '^[A-Za-z0-9.-]+$'
    validate_value PCS_APRS_GPSD_PORT "${PCS_APRS_GPSD_PORT}" '^[0-9]{1,5}$'
    validate_value PCS_APRS_BEACON_INTERVAL "${PCS_APRS_BEACON_INTERVAL}" '^[0-9]{1,2}:[0-9]{2}$'
    validate_value PCS_APRS_BEACON_TYPE "${PCS_APRS_BEACON_TYPE}" '^(gps-tracker|fixed)$'
    validate_value PCS_APRS_BEACON_PATH "${PCS_APRS_BEACON_PATH}" '^(direct|not selected|[A-Z0-9,-]{1,40})$'
    validate_value PCS_APRS_BEACON_SENDTO "${PCS_APRS_BEACON_SENDTO}" '^(BOTH|IG|[0-9])$'
    validate_value PCS_APRS_BEACON_SYMBOL "${PCS_APRS_BEACON_SYMBOL}" '^(not selected|[A-Za-z0-9 _/-]{1,40})$'
    validate_value PCS_APRS_BEACON_OVERLAY "${PCS_APRS_BEACON_OVERLAY}" '^[A-Za-z0-9]$'
    validate_value PCS_APRS_BEACON_COMMENT "${PCS_APRS_BEACON_COMMENT}" '^[A-Za-z0-9 .,_/+:-]{1,80}$'
    validate_value PCS_APRS_DIGIPEAT_ALIAS_PATTERN "${PCS_APRS_DIGIPEAT_ALIAS_PATTERN}" '^[A-Za-z0-9^$.*+?()|\\-]{1,80}$'
    validate_value PCS_APRS_DIGIPEAT_WIDE_PATTERN "${PCS_APRS_DIGIPEAT_WIDE_PATTERN}" '^[A-Za-z0-9^$.*+?()|\\-]{1,80}$'
    validate_value PCS_APRS_DIGIPEAT_PREEMPTIVE "${PCS_APRS_DIGIPEAT_PREEMPTIVE}" '^(OFF|DROP|MARK|TRACE)$'
    validate_value PCS_APRS_DIGIPEAT_FILTER "${PCS_APRS_DIGIPEAT_FILTER}" '^[A-Za-z0-9_/*,|&!().:+ -]{1,160}$'
    validate_value PCS_APRS_DIGIPEAT_DEDUPE_SECONDS "${PCS_APRS_DIGIPEAT_DEDUPE_SECONDS}" '^[0-9]{1,4}$'
    validate_value PCS_APRS_PTT_GPIO_LINE "${PCS_APRS_PTT_GPIO_LINE}" '^[0-9]{1,3}$'
    validate_value PCS_APRS_PTT_ACTIVE_LEVEL "${PCS_APRS_PTT_ACTIVE_LEVEL}" '^(high|low)$'
    validate_value PCS_APRS_LOG_RETENTION_DAYS "${PCS_APRS_LOG_RETENTION_DAYS}" '^[1-9][0-9]{0,2}$'
    for timing_value in PCS_APRS_DWAIT PCS_APRS_SLOTTIME PCS_APRS_PERSIST PCS_APRS_TXDELAY PCS_APRS_TXTAIL; do
        validate_value "${timing_value}" "${!timing_value}" '^[0-9]{1,4}$'
    done
    validate_value PCS_APRS_FULLDUP "${PCS_APRS_FULLDUP}" '^(ON|OFF)$'
    validate_value PCS_APRS_RADIO_DEVICE "${PCS_APRS_RADIO_DEVICE}" '^/dev/[A-Za-z0-9._/-]+$'
    validate_value PCS_APRS_RADIO_BAUD "${PCS_APRS_RADIO_BAUD}" '^9600$'
    validate_value PCS_APRS_RADIO_BANDWIDTH_KHZ "${PCS_APRS_RADIO_BANDWIDTH_KHZ}" '^(12|25)$'
    validate_value PCS_APRS_RADIO_TX_FREQUENCY_MHZ "${PCS_APRS_RADIO_TX_FREQUENCY_MHZ}" '^[0-9]{3}[.][0-9]{4}$'
    validate_value PCS_APRS_RADIO_RX_FREQUENCY_MHZ "${PCS_APRS_RADIO_RX_FREQUENCY_MHZ}" '^[0-9]{3}[.][0-9]{4}$'
    validate_value PCS_APRS_RADIO_TX_TONE "${PCS_APRS_RADIO_TX_TONE}" '^([0-9]{4}|[0-9]{3}[A-Z])$'
    validate_value PCS_APRS_RADIO_RX_TONE "${PCS_APRS_RADIO_RX_TONE}" '^([0-9]{4}|[0-9]{3}[A-Z])$'
    validate_value PCS_APRS_RADIO_SQUELCH "${PCS_APRS_RADIO_SQUELCH}" '^[0-8]$'
    validate_value PCS_APRS_RADIO_VOLUME "${PCS_APRS_RADIO_VOLUME}" '^[1-8]$'
    for radio_switch in PCS_APRS_RADIO_PRE_DE_EMPHASIS PCS_APRS_RADIO_HIGH_PASS PCS_APRS_RADIO_LOW_PASS PCS_APRS_RADIO_TX_TAIL; do
        validate_value "${radio_switch}" "${!radio_switch}" '^(on|off)$'
    done

    for boolean_value in PCS_APRS_IGATE PCS_APRS_GPSD PCS_APRS_BEACON PCS_APRS_BEACON_ALTITUDE PCS_APRS_DIGIPEAT PCS_APRS_TX_ENABLED PCS_APRS_FX25_TX PCS_APRS_LOGGING PCS_APRS_RADIO_INIT PCS_APRS_RX_AUDIO_VALIDATED PCS_APRS_RADIO_CHANNEL_VALIDATED PCS_APRS_PTT_VALIDATED PCS_APRS_TX_AUDIO_VALIDATED PCS_APRS_TX_TIMING_VALIDATED; do
        validate_value "${boolean_value}" "${!boolean_value}" '^(yes|no)$'
    done

    if (( PCS_APRS_AGW_PORT > 65535 || PCS_APRS_KISS_PORT > 65535 || PCS_APRS_GPSD_PORT > 65535 )); then
        echo "ERROR: APRS TCP/UDP ports must not exceed 65535." >&2
        failed=1
    fi
    if (( (PCS_APRS_AGW_PORT > 0 && PCS_APRS_AGW_PORT < 1024) || (PCS_APRS_KISS_PORT > 0 && PCS_APRS_KISS_PORT < 1024) )); then
        echo "ERROR: AGW and KISS listeners must be disabled or use ports 1024 through 65535." >&2
        failed=1
    fi

    return "${failed}"
}

render_config() {
    local profile="$1"
    local passcode="${2:-<APRS-IS-passcode>}"
    local audio_input="${PCS_APRS_AUDIO_INPUT}"
    local audio_output="null"
    local ptt_gpio="${PCS_APRS_PTT_GPIO_LINE}"
    local beacon_via=""
    local beacon_sendto
    local beacon_destinations=()

    if [[ "${profile}" != "rx" && "${profile}" != "tx" ]]; then
        echo "ERROR: PROFILE must be rx or tx." >&2
        return 2
    fi
    validate_profile_values || return 1

    if [[ "${audio_input}" == "auto" ]]; then
        audio_input="plughw:CARD=PCS_AUDIO,DEV=0"
    fi
    if [[ "${profile}" == "tx" ]]; then
        audio_output="${PCS_APRS_AUDIO_OUTPUT}"
        [[ "${audio_output}" != "auto" ]] || audio_output="plughw:CARD=PCS_AUDIO,DEV=0"
        if [[ "${PCS_APRS_PTT_ACTIVE_LEVEL}" == "low" ]]; then
            ptt_gpio="-${ptt_gpio}"
        fi
    fi

    cat <<EOF
# Generated by PCS setup-direwolf-aprs.sh for the ${profile} profile.
# Frequency label: ${PCS_APRS_FREQUENCY}
# Do not edit this generated file; change config/pcs-install.conf and render again.
ADEVICE ${audio_input} ${audio_output}
ARATE ${PCS_APRS_SAMPLE_RATE}
ACHANNELS ${PCS_APRS_AUDIO_CHANNELS}

CHANNEL 0
MYCALL ${PCS_APRS_CALLSIGN}
MODEM ${PCS_APRS_MODEM}
EOF

    if [[ "${profile}" == "tx" ]]; then
        cat <<EOF
DWAIT ${PCS_APRS_DWAIT}
SLOTTIME ${PCS_APRS_SLOTTIME}
PERSIST ${PCS_APRS_PERSIST}
TXDELAY ${PCS_APRS_TXDELAY}
TXTAIL ${PCS_APRS_TXTAIL}
FULLDUP ${PCS_APRS_FULLDUP}
EOF
        if [[ "${PCS_APRS_PTT_METHOD}" == "gpio" ]]; then
            echo "PTT GPIOD gpiochip0 ${ptt_gpio}"
        else
            echo "# BLOCKED: PCS live generator currently supports only the selected GPIO PTT profile."
        fi
    fi

    cat <<EOF

AGWPORT ${PCS_APRS_AGW_PORT}
KISSPORT ${PCS_APRS_KISS_PORT}
EOF

    if [[ "${PCS_APRS_GPSD}" == "yes" ]]; then
        echo "GPSD ${PCS_APRS_GPSD_HOST} ${PCS_APRS_GPSD_PORT}"
    fi

    if [[ "${PCS_APRS_IGATE}" == "yes" ]]; then
        cat <<EOF
IGSERVER ${PCS_APRS_IGATE_SERVER}
IGLOGIN ${PCS_APRS_CALLSIGN} ${passcode}
EOF
        if [[ "${PCS_APRS_IGATE_RF_TO_IS_FILTER}" != "all-eligible" ]]; then
            echo "FILTER 0 IG ${PCS_APRS_IGATE_RF_TO_IS_FILTER}"
        fi
        if [[ "${profile}" == "tx" && "${PCS_APRS_IGATE_MODE}" == "two-way" ]]; then
            if [[ "${PCS_APRS_IGATE_TX_PATH}" == "direct" ]]; then
                echo "IGTXVIA 0"
            else
                echo "IGTXVIA 0 ${PCS_APRS_IGATE_TX_PATH}"
            fi
            echo "IGTXLIMIT ${PCS_APRS_IGATE_TX_LIMIT_1M} ${PCS_APRS_IGATE_TX_LIMIT_5M}"
            if [[ "${PCS_APRS_IGATE_IS_TO_RF_FILTER}" != "normal-messages" ]]; then
                echo "FILTER IG 0 ${PCS_APRS_IGATE_IS_TO_RF_FILTER}"
            fi
        fi
    fi

    if [[ "${profile}" == "tx" && "${PCS_APRS_BEACON}" == "yes" ]]; then
        if [[ "${PCS_APRS_BEACON_PATH}" != "direct" && "${PCS_APRS_BEACON_PATH}" != "not selected" ]]; then
            beacon_via=" via=${PCS_APRS_BEACON_PATH}"
        fi
        if [[ "${PCS_APRS_BEACON_SYMBOL}" == "not selected" ]]; then
            echo "# BLOCKED: tracker beacon symbol is not selected."
        elif [[ "${PCS_APRS_BEACON_TYPE}" == "gps-tracker" ]]; then
            if [[ "${PCS_APRS_BEACON_SENDTO}" == "BOTH" ]]; then
                beacon_destinations=(0 IG)
            else
                beacon_destinations=("${PCS_APRS_BEACON_SENDTO}")
            fi
            for beacon_sendto in "${beacon_destinations[@]}"; do
                printf 'TBEACON SENDTO=%s DELAY=0:30 EVERY=%s' \
                    "${beacon_sendto}" "${PCS_APRS_BEACON_INTERVAL}"
                if [[ "${beacon_sendto}" != "IG" ]]; then
                    printf '%s' "${beacon_via}"
                fi
                printf ' SYMBOL="%s" OVERLAY=%s' \
                    "${PCS_APRS_BEACON_SYMBOL}" "${PCS_APRS_BEACON_OVERLAY}"
                [[ "${PCS_APRS_BEACON_ALTITUDE}" == "yes" ]] && printf ' ALT=1'
                printf ' COMMENT="%s"\n' "${PCS_APRS_BEACON_COMMENT}"
            done
        else
            echo "# BLOCKED: fixed beacon generation requires reviewed coordinates."
        fi
    fi

    if [[ "${profile}" == "tx" && "${PCS_APRS_DIGIPEAT}" == "yes" ]]; then
        printf 'DIGIPEAT 0 0 %s %s' "${PCS_APRS_DIGIPEAT_ALIAS_PATTERN}" "${PCS_APRS_DIGIPEAT_WIDE_PATTERN}"
        [[ "${PCS_APRS_DIGIPEAT_PREEMPTIVE}" == "OFF" ]] || printf ' %s' "${PCS_APRS_DIGIPEAT_PREEMPTIVE}"
        printf '\n'
        echo "DEDUPE ${PCS_APRS_DIGIPEAT_DEDUPE_SECONDS}"
        if [[ "${PCS_APRS_DIGIPEAT_FILTER}" != "all-eligible" ]]; then
            echo "FILTER 0 0 ${PCS_APRS_DIGIPEAT_FILTER}"
        fi
    fi

    if [[ "${profile}" == "tx" && "${PCS_APRS_FX25_TX}" == "yes" ]]; then
        echo "FX25TX 1"
    fi
    if [[ "${PCS_APRS_LOGGING}" == "yes" ]]; then
        echo "LOGDIR ${LOG_DIR}"
    fi
}

validate_rendered_config() {
    local profile="$1"
    local config_file="$2"
    local failed=0

    if grep -Eq '^(ADEVICE|MYCALL|PTT|TBEACON|PBEACON|IGLOGIN|IGSERVER|KISSPORT|AGWPORT|DIGIPEAT|FX25TX|LOGDIR).*\b(auto|not selected)\b' "${config_file}"; then
        echo "ERROR: rendered configuration contains an unresolved active placeholder." >&2
        failed=1
    fi
    for required in '^ADEVICE ' '^MYCALL ' '^MODEM ' '^KISSPORT '; do
        if ! grep -Eq "${required}" "${config_file}"; then
            echo "ERROR: rendered configuration is missing ${required}." >&2
            failed=1
        fi
    done

    if [[ "${profile}" == "rx" ]]; then
        if ! grep -Eq '^ADEVICE [^ ]+ null$' "${config_file}"; then
            echo "ERROR: receive profile must discard all transmit audio." >&2
            failed=1
        fi
        if grep -Eq '^(PTT|IGTXVIA|IGTXLIMIT|TBEACON|PBEACON|DIGIPEAT|FX25TX)[[:space:]]' "${config_file}"; then
            echo "ERROR: receive profile contains an RF transmit directive." >&2
            failed=1
        fi
    else
        for required in '^PTT GPIOD gpiochip0 ' '^DIGIPEAT '; do
            if ! grep -Eq "${required}" "${config_file}"; then
                echo "ERROR: transmit profile is missing ${required}." >&2
                failed=1
            fi
        done
        if [[ "${PCS_APRS_FX25_TX}" == "yes" ]] && ! grep -Eq '^FX25TX ' "${config_file}"; then
            echo "ERROR: transmit profile is missing the selected FX.25 directive." >&2
            failed=1
        fi
        if [[ "${PCS_APRS_BEACON}" == "yes" ]] && ! grep -Eq '^TBEACON ' "${config_file}"; then
            echo "ERROR: transmit profile is missing the selected tracker beacon." >&2
            failed=1
        fi
        if [[ "${PCS_APRS_BEACON}" == "yes" && "${PCS_APRS_BEACON_SENDTO}" == "BOTH" ]]; then
            if ! grep -Eq '^TBEACON SENDTO=0 ' "${config_file}" \
                || ! grep -Eq '^TBEACON SENDTO=IG ' "${config_file}"; then
                echo "ERROR: dual-path tracker profile requires both RF and APRS-IS beacons." >&2
                failed=1
            fi
        fi
    fi

    return "${failed}"
}

direwolf_numeric_version() {
    direwolf_version | grep -Eo '[0-9]+([.][0-9]+){1,2}' | head -n 1 || true
}

version_at_least() {
    local actual="$1"
    local required="$2"
    [[ -n "${actual}" ]] && [[ "$(printf '%s\n%s\n' "${required}" "${actual}" | sort -V | head -n 1)" == "${required}" ]]
}

ensure_supported_direwolf() {
    local installed_version=""
    local source_dir=""
    local actual_commit=""

    installed_version="$(direwolf_numeric_version 2>/dev/null || true)"
    if version_at_least "${installed_version}" "${DIREWOLF_MIN_VERSION}"; then
        echo "Dire Wolf ${installed_version} already satisfies PCS ${DIREWOLF_MIN_VERSION}+ requirements."
        return 0
    fi

    echo "Debian supplied Dire Wolf ${installed_version:-unknown}; PCS requires ${DIREWOLF_MIN_VERSION}+ for this profile."
    echo "Building pinned stable Dire Wolf ${DIREWOLF_SOURCE_VERSION} (${DIREWOLF_SOURCE_COMMIT})..."
    source_dir="$(mktemp -d /tmp/pcs-direwolf-source.XXXXXX)"

    if ! (
        set -Eeuo pipefail
        git clone --quiet --depth 1 --branch "${DIREWOLF_SOURCE_VERSION}" \
            "${DIREWOLF_SOURCE_URL}" "${source_dir}"
        actual_commit="$(git -C "${source_dir}" rev-parse HEAD)"
        if [[ "${actual_commit}" != "${DIREWOLF_SOURCE_COMMIT}" ]]; then
            echo "ERROR: Dire Wolf tag resolved to unexpected commit ${actual_commit}." >&2
            exit 1
        fi
        cmake -S "${source_dir}" -B "${source_dir}/build" -DCMAKE_BUILD_TYPE=Release
        cmake --build "${source_dir}/build" --parallel "$(nproc)"
        sudo cmake --install "${source_dir}/build"
    ); then
        rm -rf -- "${source_dir}"
        echo "ERROR: failed to build the pinned Dire Wolf ${DIREWOLF_SOURCE_VERSION} source." >&2
        return 1
    fi
    rm -rf -- "${source_dir}"
    hash -r

    installed_version="$(direwolf_numeric_version 2>/dev/null || true)"
    if ! version_at_least "${installed_version}" "${DIREWOLF_MIN_VERSION}"; then
        echo "ERROR: installed Dire Wolf ${installed_version:-unknown} does not satisfy ${DIREWOLF_MIN_VERSION}+." >&2
        return 1
    fi
    echo "Installed supported Dire Wolf ${installed_version} at $(command -v direwolf)."
}

show_capabilities() {
    local help_text=""
    local packet_help_text=""
    local version="not installed"
    local os_codename="unknown"
    local service_config="unknown"

    [[ -r /etc/os-release ]] && os_codename="$(. /etc/os-release; echo "${VERSION_CODENAME:-unknown}")"
    if command -v direwolf >/dev/null 2>&1; then
        help_text="$(direwolf -t 0 -h 2>&1 || true)"
        version="$(direwolf_numeric_version)"
    fi
    if command -v gen_packets >/dev/null 2>&1; then
        packet_help_text="$(gen_packets -h 2>&1 || true)"
    fi
    if systemctl cat direwolf.service 2>/dev/null | grep -Eq '(/etc/direwolf[.]conf|WorkingDirectory=/etc)'; then
        service_config="/etc/direwolf.conf"
    fi

    echo "=== PCS Dire Wolf Capability Report ==="
    echo "Dire Wolf version:      ${version}"
    echo "OS codename:            ${os_codename}"
    echo "gpsd compiled support:  $(grep -qi gpsd <<<"${help_text}" && echo yes || echo no)"
    echo "libgpiod compiled support: $(grep -qi libgpiod <<<"${help_text}" && echo yes || echo no)"
    echo "GPIO helper available:  $(command -v gpioinfo >/dev/null 2>&1 && echo yes || echo no)"
    echo "nftables available:     $(command -v nft >/dev/null 2>&1 && echo yes || echo no)"
    echo "gen_packets available:  $(command -v gen_packets >/dev/null 2>&1 && echo yes || echo no)"
    echo "atest available:        $(command -v atest >/dev/null 2>&1 && echo yes || echo no)"
    echo "FX.25 test generation:  $(grep -Eq -- '-X' <<<"${packet_help_text}" && echo yes || echo no)"
    echo "Variable-speed fixtures: $(grep -Eq -- '-v' <<<"${packet_help_text}" && echo yes || echo no)"
    echo "Service config target:   ${service_config}"

    if [[ "${os_codename}" == "trixie" && "${version}" != "not installed" ]] && ! version_at_least "${version}" "1.8"; then
        echo "WARNING: Raspberry Pi OS trixie GPIO PTT requires Dire Wolf/libgpiod compatibility; PCS requires version 1.8 or newer for this profile."
    fi
}

collect_activation_blockers() {
    local profile="$1"
    local help_text=""
    local version=""
    local os_codename="unknown"

    ACTIVATION_BLOCKERS=()
    validate_profile_values || record_blocker "one or more desired-profile values failed syntax validation"
    command -v direwolf >/dev/null 2>&1 || record_blocker "Dire Wolf is not installed"
    if command -v direwolf >/dev/null 2>&1; then
        help_text="$(direwolf -t 0 -h 2>&1 || true)"
    fi
    command -v nft >/dev/null 2>&1 || record_blocker "nftables is not installed for LAN-only AGW/KISS enforcement"
    id direwolf >/dev/null 2>&1 || record_blocker "the unprivileged direwolf service account is missing"
    if id direwolf >/dev/null 2>&1; then
        id -nG direwolf | tr ' ' '\n' | grep -Fxq audio || record_blocker "the direwolf service account is not in the audio group"
    fi
    if ! systemctl cat direwolf.service 2>/dev/null | grep -Eq '(/etc/direwolf[.]conf|WorkingDirectory=/etc)'; then
        record_blocker "direwolf.service is not confirmed to load /etc/direwolf.conf"
    fi

    if [[ "${PCS_APRS_AUDIO_INPUT}" == "auto" ]]; then
        record_blocker "ALSA capture device is still auto"
    fi
    [[ "${PCS_APRS_RX_AUDIO_VALIDATED}" == "yes" ]] || record_blocker "receive audio decoding has not been validated"
    [[ "${PCS_APRS_RADIO_CHANNEL_VALIDATED}" == "yes" ]] || record_blocker "radio programming for ${PCS_APRS_FREQUENCY} has not been validated"
    [[ "${PCS_APRS_RADIO_INIT}" == "yes" ]] || record_blocker "SA818S boot-time initialization is not enabled"
    uart_boot_enabled || record_blocker "Pi UART boot enablement is missing; run --prepare-uart and reboot"
    serial_console_disabled || record_blocker "the Pi serial login console still owns or may own the SA818S UART"
    [[ -e "${PCS_APRS_RADIO_DEVICE}" ]] || record_blocker "SA818S UART ${PCS_APRS_RADIO_DEVICE} is unavailable"

    if [[ "${PCS_APRS_GPSD}" == "yes" ]]; then
        if command -v direwolf >/dev/null 2>&1; then
            grep -qi gpsd <<<"${help_text}" || record_blocker "Dire Wolf does not report compiled-in gpsd support"
        fi
        systemctl is-active --quiet gpsd.service 2>/dev/null || record_blocker "gpsd.service is not active"
    fi

    if [[ "${profile}" == "tx" ]]; then
        [[ "${PCS_APRS_TX_ENABLED}" == "yes" ]] || record_blocker "the desired profile does not enable RF transmit"
        [[ "${PCS_APRS_AUDIO_OUTPUT}" != "auto" && "${PCS_APRS_AUDIO_OUTPUT}" != "null" ]] || record_blocker "ALSA playback device is unresolved or null"
        [[ "${PCS_APRS_PTT_METHOD}" == "gpio" ]] || record_blocker "PCS live generation currently supports only the selected GPIO PTT method"
        [[ "${PCS_APRS_PTT_GPIO_LINE}" == "6" ]] || record_blocker "configured PTT GPIO${PCS_APRS_PTT_GPIO_LINE} conflicts with the finalized GPIO6 schematic allocation"
        [[ "${PCS_APRS_PTT_VALIDATED}" == "yes" ]] || record_blocker "GPIO6/EasyDigi PTT polarity has not been bench-validated"
        [[ "${PCS_APRS_TX_AUDIO_VALIDATED}" == "yes" ]] || record_blocker "transmit audio level and deviation have not been validated"
        [[ "${PCS_APRS_TX_TIMING_VALIDATED}" == "yes" ]] || record_blocker "DWAIT/SLOTTIME/PERSIST/TXDELAY/TXTAIL have not been validated"
        command -v gpioinfo >/dev/null 2>&1 || record_blocker "libgpiod gpioinfo is unavailable"
        grep -qi 'libgpiod' <<<"${help_text}" || record_blocker "Dire Wolf does not report compiled-in libgpiod support"
        getent group gpio >/dev/null 2>&1 || record_blocker "the Raspberry Pi gpio group is unavailable"
        if id direwolf >/dev/null 2>&1; then
            id -nG direwolf | tr ' ' '\n' | grep -Fxq gpio || record_blocker "the direwolf service account is not in the gpio group"
        fi
        if [[ "${PCS_APRS_BEACON}" == "yes" ]]; then
            [[ "${PCS_APRS_BEACON_PATH}" != "not selected" ]] || record_blocker "beacon RF path is not selected"
            [[ "${PCS_APRS_BEACON_SYMBOL}" != "not selected" ]] || record_blocker "beacon APRS symbol is not selected"
        fi
        [[ -r /etc/os-release ]] && os_codename="$(. /etc/os-release; echo "${VERSION_CODENAME:-unknown}")"
        version="$(direwolf_numeric_version 2>/dev/null || true)"
        if [[ "${os_codename}" == "trixie" ]] && ! version_at_least "${version}" "1.8"; then
            record_blocker "Raspberry Pi OS trixie GPIO PTT requires Dire Wolf 1.8 or newer"
        fi
    fi
}

report_activation_blockers() {
    local profile="$1"
    local blocker

    collect_activation_blockers "${profile}"
    if (( ${#ACTIVATION_BLOCKERS[@]} == 0 )); then
        echo "No ${profile} activation blockers were found."
        return 0
    fi

    echo "${profile^^} activation is blocked:"
    for blocker in "${ACTIVATION_BLOCKERS[@]}"; do
        echo "- ${blocker}"
    done
    return 1
}

validate_config_command() {
    local profile="$1"
    local temp_file
    local failed=0

    temp_file="$(mktemp)"
    if ! render_config "${profile}" >"${temp_file}"; then
        rm -f -- "${temp_file}"
        return 1
    fi
    validate_rendered_config "${profile}" "${temp_file}" || failed=1
    report_activation_blockers "${profile}" || failed=1
    rm -f -- "${temp_file}"
    if (( failed == 0 )); then
        echo "Rendered ${profile} configuration passed PCS policy validation."
    fi
    return "${failed}"
}

direwolf_version() {
    direwolf -t 0 -h 2>&1 | sed -n '1p' || true
}

show_check() {
    local audio_cards=""
    local help_text=""
    local service_state="not installed"
    local service_enabled="not installed"

    echo
    echo "=== PCS Dire Wolf / APRS Check ==="
    echo
    echo "Install state: ${PCS_SETUP_APRS}"
    echo "Active mode:  ${PCS_APRS_ACTIVE_MODE}"
    echo "Desired role: ${PCS_APRS_ROLE}"
    echo "Callsign:     ${PCS_APRS_CALLSIGN:-not selected}"
    echo "Frequency:    ${PCS_APRS_FREQUENCY}"
    echo "Modem:        ${PCS_APRS_MODEM} baud"
    echo "Audio input:  ${PCS_APRS_AUDIO_INPUT}"
    echo "Audio output: ${PCS_APRS_AUDIO_OUTPUT}"
    echo "ALSA levels:  ${PCS_APRS_AUDIO_CARD} / TX ${PCS_APRS_PLAYBACK_LEVEL} / RX ${PCS_APRS_CAPTURE_LEVEL} / AGC ${PCS_APRS_AGC_STATE}"
    echo "Radio UART:   ${PCS_APRS_RADIO_DEVICE} at ${PCS_APRS_RADIO_BAUD}; init=${PCS_APRS_RADIO_INIT}"
    echo "UART boot:    $(uart_boot_enabled && echo enabled || echo missing); console=$(serial_console_disabled && echo disabled || echo enabled-or-unknown)"
    echo "Radio group:  ${PCS_APRS_RADIO_BANDWIDTH_KHZ} kHz / TX ${PCS_APRS_RADIO_TX_FREQUENCY_MHZ} / RX ${PCS_APRS_RADIO_RX_FREQUENCY_MHZ} / SQ ${PCS_APRS_RADIO_SQUELCH}"
    echo "PTT method:   ${PCS_APRS_PTT_METHOD}"
    echo "PTT hardware: ${PCS_APRS_PTT_INTERFACE} / GPIO ${PCS_APRS_PTT_GPIO_LINE} / ${PCS_APRS_PTT_ACTIVE_LEVEL}"
    echo "AGW / KISS:   ${PCS_APRS_AGW_PORT} / ${PCS_APRS_KISS_PORT}"
    echo "APRS-IS:      ${PCS_APRS_IGATE} (${PCS_APRS_IGATE_MODE}; ${PCS_APRS_IGATE_SERVER})"
    echo "RF -> IS:     ${PCS_APRS_IGATE_RF_TO_IS_FILTER}"
    echo "IS -> RF:     ${PCS_APRS_IGATE_IS_TO_RF_FILTER} via ${PCS_APRS_IGATE_TX_PATH}"
    echo "IGate limits: ${PCS_APRS_IGATE_TX_LIMIT_1M}/minute; ${PCS_APRS_IGATE_TX_LIMIT_5M}/5 minutes"
    echo "GPSD:         ${PCS_APRS_GPSD} (${PCS_APRS_GPSD_HOST}:${PCS_APRS_GPSD_PORT})"
    echo "TX / beacon:  ${PCS_APRS_TX_ENABLED} / ${PCS_APRS_BEACON}"
    echo "Beacon plan:  ${PCS_APRS_BEACON_TYPE} / ${PCS_APRS_BEACON_INTERVAL} / ${PCS_APRS_BEACON_PATH} / ${PCS_APRS_BEACON_SENDTO}"
    echo "Digipeat:     ${PCS_APRS_DIGIPEAT} (${PCS_APRS_DIGIPEAT_MODE}; alias ${PCS_APRS_DIGIPEAT_ALIAS})"
    echo "Digi rule:    ${PCS_APRS_DIGIPEAT_ALIAS_PATTERN} / ${PCS_APRS_DIGIPEAT_WIDE_PATTERN} / ${PCS_APRS_DIGIPEAT_PREEMPTIVE}"
    echo "Digi filter:  ${PCS_APRS_DIGIPEAT_FILTER}; dedupe ${PCS_APRS_DIGIPEAT_DEDUPE_SECONDS}s"
    echo "FX.25 TX:     ${PCS_APRS_FX25_TX}"
    echo "Log retention: ${PCS_APRS_LOG_RETENTION_DAYS} days"
    echo "Validation:   RX audio=${PCS_APRS_RX_AUDIO_VALIDATED}; radio=${PCS_APRS_RADIO_CHANNEL_VALIDATED}; PTT=${PCS_APRS_PTT_VALIDATED}; TX audio=${PCS_APRS_TX_AUDIO_VALIDATED}; timing=${PCS_APRS_TX_TIMING_VALIDATED}"

    if command -v direwolf >/dev/null 2>&1; then
        echo "Dire Wolf:    $(command -v direwolf)"
        echo "Version:      $(direwolf_version)"
        help_text="$(direwolf -t 0 -h 2>&1 || true)"
        if grep -qi 'gpsd' <<<"${help_text}"; then
            echo "GPSD support:  compiled in"
        else
            echo "GPSD support:  NOT reported by this Dire Wolf build"
        fi
    else
        echo "Dire Wolf:    not installed"
    fi

    if [[ "${PCS_APRS_GPSD}" == "yes" ]]; then
        echo "gpsd service:  $(systemctl is-active gpsd.service 2>/dev/null || true)"
    fi

    if systemctl list-unit-files direwolf.service --no-legend 2>/dev/null | grep -q '^direwolf[.]service'; then
        service_state="$(systemctl is-active direwolf.service 2>/dev/null || true)"
        service_enabled="$(systemctl is-enabled direwolf.service 2>/dev/null || true)"
    fi

    echo "Service:      ${service_state} (${service_enabled})"
    echo "Radio init:   $(systemctl is-active pcs-sa818.service 2>/dev/null || true)"
    echo "Audio setup:  $(systemctl is-active pcs-aprs-audio.service 2>/dev/null || true)"
    echo "Client firewall: $(systemctl is-active pcs-aprs-kiss-firewall.service 2>/dev/null || true)"
    echo "PCS template: $(sudo -n test -r "${TEMPLATE_DST}" 2>/dev/null && echo installed || echo 'restricted or missing')"
    echo "Live config:  $(sudo -n test -s "${DIREWOLF_CONFIG}" 2>/dev/null && echo present || echo 'not present')"
    echo "Test tools:   $(command -v gen_packets >/dev/null 2>&1 && command -v atest >/dev/null 2>&1 && echo available || echo missing)"
    echo
    echo "ALSA capture devices:"

    if command -v arecord >/dev/null 2>&1; then
        audio_cards="$(arecord -l 2>&1 || true)"
        if [[ -n "${audio_cards}" ]]; then
            printf '%s\n' "${audio_cards}"
        else
            echo "No capture-device output returned."
        fi
    else
        echo "arecord is not installed."
    fi

    echo
    if [[ "${PCS_SETUP_APRS}" == "staged" ]]; then
        echo "State is correct for pre-hardware staging: software present, service disabled."
    elif [[ "${PCS_SETUP_APRS}" == "yes" ]]; then
        echo "APRS is marked active; complete the full hardware and on-air validation checklist."
    else
        echo "APRS is not selected in ${INSTALL_CONFIG}."
    fi
}

ensure_sudo() {
    if ! sudo -n true 2>/dev/null; then
        sudo -v
    fi
}

write_audio_environment() {
    local destination="$1"

    printf 'PCS_APRS_AUDIO_CARD=%q\nPCS_APRS_PLAYBACK_CONTROL=%q\nPCS_APRS_PLAYBACK_LEVEL=%q\nPCS_APRS_CAPTURE_CONTROL=%q\nPCS_APRS_CAPTURE_LEVEL=%q\nPCS_APRS_AGC_CONTROL=%q\nPCS_APRS_AGC_STATE=%q\n' \
        "${PCS_APRS_AUDIO_CARD}" "${PCS_APRS_PLAYBACK_CONTROL}" "${PCS_APRS_PLAYBACK_LEVEL}" \
        "${PCS_APRS_CAPTURE_CONTROL}" "${PCS_APRS_CAPTURE_LEVEL}" \
        "${PCS_APRS_AGC_CONTROL}" "${PCS_APRS_AGC_STATE}" >"${destination}"
}

set_rx_level() {
    local requested="${1:-}"
    local numeric="${requested%\%}"
    local previous_level="${PCS_APRS_CAPTURE_LEVEL}"
    local temp_dir=""
    local audio_env=""
    local backup_file=""
    local timestamp=""

    require_normal_user
    if [[ ! "${numeric}" =~ ^[0-9]{1,3}$ ]] || (( 10#${numeric} > 100 )); then
        echo "ERROR: --set-rx-level requires a percentage from 0 through 100." >&2
        return 2
    fi

    PCS_APRS_CAPTURE_LEVEL="${numeric}%"
    set_install_config_value PCS_APRS_CAPTURE_LEVEL "${PCS_APRS_CAPTURE_LEVEL}"

    if ! systemctl is-enabled --quiet pcs-aprs-audio.service 2>/dev/null; then
        echo "Recorded PCS_APRS_CAPTURE_LEVEL=${PCS_APRS_CAPTURE_LEVEL} in ${INSTALL_CONFIG}."
        echo "PCS APRS audio restoration is not enabled; no mixer or service state changed."
        return 0
    fi

    ensure_sudo
    if ! sudo test -s "${APRS_CONFIG_DIR}/audio.conf"; then
        echo "Recorded PCS_APRS_CAPTURE_LEVEL=${PCS_APRS_CAPTURE_LEVEL} in ${INSTALL_CONFIG}."
        echo "No active PCS APRS audio profile was found; no mixer or service state changed."
        return 0
    fi
    temp_dir="$(mktemp -d)"
    audio_env="${temp_dir}/audio.conf"
    write_audio_environment "${audio_env}"
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    sudo install -d -o root -g direwolf -m 0750 "${BACKUP_DIR}"
    backup_file="$(sudo mktemp "${BACKUP_DIR}/audio.conf.${timestamp}.XXXXXX")"
    sudo cp --preserve=mode,ownership,timestamps -- "${APRS_CONFIG_DIR}/audio.conf" "${backup_file}"
    sudo install -o root -g direwolf -m 0640 "${audio_env}" "${APRS_CONFIG_DIR}/audio.conf"

    if ! sudo systemctl restart pcs-aprs-audio.service; then
        echo "ERROR: the new receive level failed verification; restoring ${previous_level}." >&2
        sudo install -o root -g direwolf -m 0640 "${backup_file}" "${APRS_CONFIG_DIR}/audio.conf"
        sudo systemctl restart pcs-aprs-audio.service || true
        set_install_config_value PCS_APRS_CAPTURE_LEVEL "${previous_level}"
        rm -rf -- "${temp_dir}"
        return 1
    fi

    rm -rf -- "${temp_dir}"
    echo "PCS APRS receive capture is now ${PCS_APRS_CAPTURE_LEVEL}."
    echo "Runtime backup: ${backup_file}"
    echo "Dire Wolf was not restarted; its live TX configuration was untouched."
}

set_tx_timing() {
    local requested_delay="${1:-}"
    local requested_tail="${2:-}"
    local delay=""
    local tail=""
    local live_delay=""
    local live_tail=""

    require_normal_user
    if [[ ! "${requested_delay}" =~ ^[0-9]{1,4}$ \
        || ! "${requested_tail}" =~ ^[0-9]{1,4}$ ]]; then
        echo "ERROR: --set-tx-timing requires TXDELAY and TXTAIL values from 0 through 9999." >&2
        return 2
    fi
    delay="$((10#${requested_delay}))"
    tail="$((10#${requested_tail}))"

    if ! systemctl is-active --quiet direwolf.service 2>/dev/null; then
        echo "ERROR: Dire Wolf must be active before commissioned TX timing can be recorded." >&2
        echo "No managed configuration was changed." >&2
        return 1
    fi

    ensure_sudo
    live_delay="$(sudo awk '$1 == "TXDELAY" { print $2; exit }' "${DIREWOLF_CONFIG}")"
    live_tail="$(sudo awk '$1 == "TXTAIL" { print $2; exit }' "${DIREWOLF_CONFIG}")"
    if [[ "${live_delay}" != "${delay}" || "${live_tail}" != "${tail}" ]]; then
        echo "ERROR: refusing to record timing that differs from active ${DIREWOLF_CONFIG}." >&2
        echo "Active: TXDELAY ${live_delay:-missing}; TXTAIL ${live_tail:-missing}." >&2
        echo "Requested: TXDELAY ${delay}; TXTAIL ${tail}." >&2
        return 1
    fi

    PCS_APRS_TXDELAY="${delay}"
    PCS_APRS_TXTAIL="${tail}"
    set_install_config_value PCS_APRS_TXDELAY "${PCS_APRS_TXDELAY}"
    set_install_config_value PCS_APRS_TXTAIL "${PCS_APRS_TXTAIL}"
    echo "Recorded TXDELAY ${PCS_APRS_TXDELAY} and TXTAIL ${PCS_APRS_TXTAIL} in ${INSTALL_CONFIG}."
    echo "The active Dire Wolf configuration already matches. No service was restarted."
    echo "Existing hardware-validation evidence was not changed."
}

find_rpi_boot_file() {
    local name="$1"

    if [[ -f "/boot/firmware/${name}" ]]; then
        printf '%s\n' "/boot/firmware/${name}"
    elif [[ -f "/boot/${name}" ]]; then
        printf '%s\n' "/boot/${name}"
    else
        return 1
    fi
}

uart_boot_enabled() {
    local config_file=""

    config_file="$(find_rpi_boot_file config.txt 2>/dev/null || true)"
    [[ -n "${config_file}" ]] || return 1
    awk -F= '
        /^[[:space:]]*enable_uart[[:space:]]*=/ {
            value=$2
            sub(/[[:space:]#].*$/, "", value)
            gsub(/[[:space:]]/, "", value)
        }
        END { exit(value == "1" ? 0 : 1) }
    ' "${config_file}"
}

serial_console_disabled() {
    local cmdline_file=""

    cmdline_file="$(find_rpi_boot_file cmdline.txt 2>/dev/null || true)"
    [[ -n "${cmdline_file}" ]] || return 1
    ! grep -Eq '(^|[[:space:]])console=(serial0|ttyAMA[0-9]*|ttyS[0-9]*),' "${cmdline_file}"
}

show_uart_status() {
    local config_file=""
    local cmdline_file=""

    config_file="$(find_rpi_boot_file config.txt 2>/dev/null || true)"
    cmdline_file="$(find_rpi_boot_file cmdline.txt 2>/dev/null || true)"
    echo "UART boot file: ${config_file:-not found}"
    echo "UART enabled:   $(uart_boot_enabled && echo yes || echo no)"
    echo "Serial console: $(serial_console_disabled && echo disabled || echo enabled-or-unknown)"
    echo "UART device:    $([[ -e "${PCS_APRS_RADIO_DEVICE}" ]] && echo present || echo missing) (${PCS_APRS_RADIO_DEVICE})"
    echo "Bluetooth:      unchanged by PCS APRS"
    [[ -n "${cmdline_file}" ]] && echo "Kernel cmdline:  ${cmdline_file}"
}

prepare_uart() {
    local config_file=""
    local cmdline_file=""
    local temp_dir=""
    local changed=0

    require_normal_user
    if [[ ! -r /proc/device-tree/model ]] || ! grep -aq 'Raspberry Pi' /proc/device-tree/model; then
        echo "ERROR: --prepare-uart is supported only on Raspberry Pi OS hardware." >&2
        return 1
    fi
    config_file="$(find_rpi_boot_file config.txt 2>/dev/null || true)"
    cmdline_file="$(find_rpi_boot_file cmdline.txt 2>/dev/null || true)"
    if [[ -z "${config_file}" || -z "${cmdline_file}" ]]; then
        echo "ERROR: Raspberry Pi boot config.txt and cmdline.txt were not both found." >&2
        return 1
    fi

    ensure_sudo
    temp_dir="$(mktemp -d)"
    cp -- "${config_file}" "${temp_dir}/config.txt"
    cp -- "${cmdline_file}" "${temp_dir}/cmdline.txt"

    if ! uart_boot_enabled; then
        printf '\n# PCS APRS SA818S UART\n[all]\nenable_uart=1\n' >>"${temp_dir}/config.txt"
    fi
    if serial_console_disabled; then
        cp -- "${cmdline_file}" "${temp_dir}/cmdline.txt"
    else
        awk '
            {
                output=""
                for (i=1; i<=NF; i++) {
                    if ($i ~ /^console=(serial0|ttyAMA[0-9]*|ttyS[0-9]*),/) continue
                    output = output (output == "" ? "" : " ") $i
                }
                print output
            }
        ' "${cmdline_file}" >"${temp_dir}/cmdline.filtered"
        mv -f -- "${temp_dir}/cmdline.filtered" "${temp_dir}/cmdline.txt"
    fi

    if ! cmp -s "${config_file}" "${temp_dir}/config.txt"; then
        if ! sudo test -e "${config_file}.pcs-pre-uart.bak"; then
            sudo cp --preserve=mode,ownership,timestamps -- "${config_file}" "${config_file}.pcs-pre-uart.bak"
        fi
        sudo cp -- "${temp_dir}/config.txt" "${config_file}"
        changed=1
    fi
    if ! cmp -s "${cmdline_file}" "${temp_dir}/cmdline.txt"; then
        if ! sudo test -e "${cmdline_file}.pcs-pre-uart.bak"; then
            sudo cp --preserve=mode,ownership,timestamps -- "${cmdline_file}" "${cmdline_file}.pcs-pre-uart.bak"
        fi
        sudo cp -- "${temp_dir}/cmdline.txt" "${cmdline_file}"
        changed=1
    fi
    rm -rf -- "${temp_dir}"

    sudo systemctl disable --now \
        serial-getty@serial0.service serial-getty@ttyAMA0.service serial-getty@ttyS0.service \
        >/dev/null 2>&1 || true

    echo "PCS APRS UART boot configuration is present; Bluetooth was not changed."
    if (( changed == 1 )); then
        echo "A reboot is required before UART activation validation can pass."
    else
        echo "No boot-file changes were needed."
    fi
    show_uart_status
}

refresh_control_panel_if_installed() {
    if [[ ! -x "${CONTROL_PANEL_SETUP}" ]]; then
        echo "WARNING: control-panel installer is missing; APRS dashboard code was not refreshed." >&2
        return 0
    fi
    if ! sudo test -s /etc/pcs-control-panel/admin.json 2>/dev/null; then
        echo "PCS control panel is not commissioned; skipping its APRS dashboard refresh."
        return 0
    fi

    echo "Refreshing the installed PCS control panel while preserving its credentials..."
    if ! "${CONTROL_PANEL_SETUP}"; then
        echo "WARNING: APRS succeeded, but the PCS control-panel refresh failed." >&2
        echo "Retry with: ./scripts/setup-pcs-control-panel.sh" >&2
    fi
}

prompt_aprs_is_passcode() {
    local first=""
    local second=""

    APRS_IS_PASSCODE=""
    if [[ "${PCS_APRS_IGATE}" != "yes" ]]; then
        return 0
    fi
    if [[ ! -t 0 ]]; then
        echo "ERROR: APRS-IS activation requires an interactive terminal for the passcode." >&2
        return 1
    fi

    while true; do
        read -r -s -p "APRS-IS passcode for ${PCS_APRS_CALLSIGN}: " first
        echo
        read -r -s -p "Confirm APRS-IS passcode: " second
        echo
        if [[ "${first}" != "${second}" ]]; then
            echo "Passcodes did not match."
            continue
        fi
        if [[ ! "${first}" =~ ^[0-9]{1,5}$ ]] || (( 10#${first} > 32767 )); then
            echo "Use the numeric APRS-IS passcode for this callsign (0 through 32767)."
            continue
        fi
        APRS_IS_PASSCODE="${first}"
        first=""
        second=""
        return 0
    done
}

install_runtime_support() {
    local temp_dir="$1"
    local kiss_env="${temp_dir}/kiss-firewall.conf"
    local audio_env="${temp_dir}/audio.conf"
    local sa818_ini="${temp_dir}/sa818.ini"
    local logrotate_config="${temp_dir}/pcs-direwolf.logrotate"
    local direwolf_override="${temp_dir}/pcs-direwolf-override.conf"
    local direwolf_bin=""

    if [[ ! -f "${KISS_FIREWALL_SRC}" || ! -f "${KISS_FIREWALL_SERVICE_SRC}" \
        || ! -f "${DIREWOLF_OVERRIDE_SRC}" || ! -f "${SA818_SRC}" \
        || ! -f "${SA818_SERVICE_SRC}" || ! -f "${APRS_AUDIO_SRC}" \
        || ! -f "${APRS_AUDIO_SERVICE_SRC}" ]]; then
        echo "ERROR: PCS APRS runtime support files are missing from the repository." >&2
        return 1
    fi

    direwolf_bin="$(command -v direwolf || true)"
    if [[ -z "${direwolf_bin}" || "${direwolf_bin}" != /* ]]; then
        echo "ERROR: no absolute Dire Wolf executable path is available." >&2
        return 1
    fi
    sed "s|@DIREWOLF_BIN@|${direwolf_bin}|g" "${DIREWOLF_OVERRIDE_SRC}" >"${direwolf_override}"

    printf 'PCS_APRS_AGW_PORT=%q\nPCS_APRS_KISS_PORT=%q\nPCS_APRS_KISS_LAN_INTERFACE=%q\nPCS_APRS_KISS_LAN_NETWORK=%q\n' \
        "${PCS_APRS_AGW_PORT}" "${PCS_APRS_KISS_PORT}" "${PCS_APRS_KISS_LAN_INTERFACE}" "${PCS_APRS_KISS_LAN_NETWORK}" >"${kiss_env}"
    write_audio_environment "${audio_env}"
    cat >"${sa818_ini}" <<EOF
[radio]
device = ${PCS_APRS_RADIO_DEVICE}
baud = ${PCS_APRS_RADIO_BAUD}
bandwidth_khz = ${PCS_APRS_RADIO_BANDWIDTH_KHZ}
tx_frequency_mhz = ${PCS_APRS_RADIO_TX_FREQUENCY_MHZ}
rx_frequency_mhz = ${PCS_APRS_RADIO_RX_FREQUENCY_MHZ}
tx_tone = ${PCS_APRS_RADIO_TX_TONE}
squelch = ${PCS_APRS_RADIO_SQUELCH}
rx_tone = ${PCS_APRS_RADIO_RX_TONE}
volume = ${PCS_APRS_RADIO_VOLUME}
pre_de_emphasis = ${PCS_APRS_RADIO_PRE_DE_EMPHASIS}
high_pass = ${PCS_APRS_RADIO_HIGH_PASS}
low_pass = ${PCS_APRS_RADIO_LOW_PASS}
tx_tail = ${PCS_APRS_RADIO_TX_TAIL}
EOF
    cat >"${logrotate_config}" <<EOF
${LOG_DIR}/*.log {
    daily
    rotate ${PCS_APRS_LOG_RETENTION_DAYS}
    missingok
    notifempty
    compress
    delaycompress
    dateext
    su direwolf direwolf
}
EOF

    sudo install -d -o root -g direwolf -m 0750 "${APRS_CONFIG_DIR}" "${BACKUP_DIR}"
    sudo install -d -o direwolf -g direwolf -m 0750 "${LOG_DIR}"
    sudo install -o root -g root -m 0755 "${KISS_FIREWALL_SRC}" "${KISS_FIREWALL_DST}"
    sudo install -o root -g root -m 0644 "${KISS_FIREWALL_SERVICE_SRC}" "${KISS_FIREWALL_SERVICE_DST}"
    sudo install -o root -g root -m 0640 "${kiss_env}" "${APRS_CONFIG_DIR}/kiss-firewall.conf"
    sudo install -o root -g root -m 0755 "${SA818_SRC}" "${SA818_DST}"
    sudo install -o root -g root -m 0644 "${SA818_SERVICE_SRC}" "${SA818_SERVICE_DST}"
    sudo install -o root -g direwolf -m 0640 "${sa818_ini}" "${APRS_CONFIG_DIR}/sa818.ini"
    sudo install -o root -g root -m 0755 "${APRS_AUDIO_SRC}" "${APRS_AUDIO_DST}"
    sudo install -o root -g root -m 0644 "${APRS_AUDIO_SERVICE_SRC}" "${APRS_AUDIO_SERVICE_DST}"
    sudo install -o root -g direwolf -m 0640 "${audio_env}" "${APRS_CONFIG_DIR}/audio.conf"
    sudo install -d -o root -g root -m 0755 "$(dirname "${DIREWOLF_OVERRIDE_DST}")"
    sudo install -o root -g root -m 0644 "${direwolf_override}" "${DIREWOLF_OVERRIDE_DST}"
    sudo install -o root -g root -m 0644 "${logrotate_config}" /etc/logrotate.d/pcs-direwolf
    sudo systemctl daemon-reload
    sudo systemctl enable pcs-sa818.service pcs-aprs-audio.service pcs-aprs-kiss-firewall.service
    sudo systemctl restart pcs-sa818.service
    sudo systemctl restart pcs-aprs-audio.service
    sudo systemctl restart pcs-aprs-kiss-firewall.service
}

restore_backup_file() {
    local backup_file="$1"
    local mode_file="${backup_file}.mode"
    local restored_mode="rx"

    sudo install -o root -g direwolf -m 0640 "${backup_file}" "${DIREWOLF_CONFIG}"
    if sudo test -s "${mode_file}"; then
        restored_mode="$(sudo sed -n '1p' "${mode_file}" | tr -d '\r\n')"
    fi
    sudo systemctl restart direwolf.service
    set_install_config_value PCS_SETUP_APRS yes
    set_install_config_value PCS_APRS_ACTIVE_MODE "${restored_mode}"
    PCS_SETUP_APRS="yes"
    PCS_APRS_ACTIVE_MODE="${restored_mode}"
    echo "Restored ${backup_file} (${restored_mode} mode)."
}

activate_profile() {
    local profile="$1"
    local temp_dir=""
    local rendered_config=""
    local backup_file=""
    local backup_mode_file=""
    local timestamp=""
    local confirmation=""

    require_normal_user
    if ! report_activation_blockers "${profile}"; then
        echo "No configuration or service state was changed."
        return 1
    fi
    if [[ "${profile}" == "tx" ]]; then
        if [[ ! -t 0 ]]; then
            echo "ERROR: --activate-tx requires an interactive terminal." >&2
            return 1
        fi
        echo "Transmit activation can key GPIO${PCS_APRS_PTT_GPIO_LINE} and emit RF immediately after Dire Wolf starts."
        read -r -p "Type ENABLE-RF-${PCS_APRS_CALLSIGN} to continue: " confirmation
        if [[ "${confirmation}" != "ENABLE-RF-${PCS_APRS_CALLSIGN}" ]]; then
            echo "Transmit activation cancelled."
            return 1
        fi
    fi
    ensure_sudo
    if sudo test -s "${DIREWOLF_CONFIG}" && [[ "${PCS_SETUP_APRS}" != "yes" ]]; then
        echo "ERROR: ${DIREWOLF_CONFIG} exists but PCS_SETUP_APRS is not yes." >&2
        echo "Refusing to replace an untracked live configuration; reconcile or back it up first." >&2
        APRS_IS_PASSCODE=""
        return 1
    fi
    prompt_aprs_is_passcode || return 1

    temp_dir="$(mktemp -d)"
    rendered_config="${temp_dir}/direwolf.conf"
    chmod 0700 "${temp_dir}"
    render_config "${profile}" "${APRS_IS_PASSCODE:-<APRS-IS-passcode>}" >"${rendered_config}"
    chmod 0600 "${rendered_config}"
    validate_rendered_config "${profile}" "${rendered_config}"
    install_runtime_support "${temp_dir}"

    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    if sudo test -s "${DIREWOLF_CONFIG}"; then
        backup_file="$(sudo mktemp "${BACKUP_DIR}/direwolf.conf.${timestamp}.XXXXXX")"
        backup_mode_file="${backup_file}.mode"
        sudo cp --preserve=mode,ownership,timestamps -- "${DIREWOLF_CONFIG}" "${backup_file}"
        printf '%s\n' "${PCS_APRS_ACTIVE_MODE}" >"${temp_dir}/previous.mode"
        sudo install -o root -g direwolf -m 0640 "${temp_dir}/previous.mode" "${backup_mode_file}"
        echo "Saved previous live configuration as ${backup_file}."
    fi

    sudo install -o root -g direwolf -m 0640 "${rendered_config}" "${DIREWOLF_CONFIG}"
    sudo systemctl enable direwolf.service
    if ! sudo systemctl restart direwolf.service; then
        echo "ERROR: direwolf.service failed after installing the ${profile} profile." >&2
        if [[ -n "${backup_file}" ]]; then
            sudo install -o root -g direwolf -m 0640 "${backup_file}" "${DIREWOLF_CONFIG}"
            sudo systemctl restart direwolf.service || true
            echo "Previous live configuration restored."
        else
            sudo rm -f -- "${DIREWOLF_CONFIG}"
            sudo systemctl disable --now direwolf.service >/dev/null 2>&1 || true
            echo "No previous configuration existed; Dire Wolf was disabled again."
        fi
        sudo journalctl -u direwolf.service -n 40 --no-pager || true
        rm -rf -- "${temp_dir}"
        APRS_IS_PASSCODE=""
        return 1
    fi

    set_install_config_value PCS_SETUP_APRS yes
    set_install_config_value PCS_APRS_ACTIVE_MODE "${profile}"
    PCS_SETUP_APRS="yes"
    PCS_APRS_ACTIVE_MODE="${profile}"
    APRS_IS_PASSCODE=""
    rm -rf -- "${temp_dir}"
    echo "Dire Wolf ${profile} profile activated successfully."
    refresh_control_panel_if_installed
    if [[ "${profile}" == "rx" ]]; then
        echo "RF transmit remains impossible in this profile: output is null and no PTT or transmit directives are present."
    fi
}

rollback_config() {
    local backup_file=""

    require_normal_user
    ensure_sudo
    backup_file="$(sudo find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'direwolf.conf.*' ! -name '*.mode' -printf '%T@ %p\n' 2>/dev/null \
        | sort -nr | awk 'NR==1 {$1=""; sub(/^ /, ""); print}')"
    if [[ -z "${backup_file}" ]]; then
        echo "ERROR: no PCS Dire Wolf configuration backup is available." >&2
        return 1
    fi
    restore_backup_file "${backup_file}"
}

prepare() {
    local was_active=0

    require_normal_user

    if [[ ! -f "${TEMPLATE_SRC}" ]]; then
        echo "ERROR: missing PCS Dire Wolf template: ${TEMPLATE_SRC}"
        exit 1
    fi

    if ! sudo -n true 2>/dev/null; then
        sudo -v
    fi

    if sudo test -s "${DIREWOLF_CONFIG}"; then
        if [[ "${PCS_SETUP_APRS}" == "yes" ]]; then
            was_active=1
            echo "An active APRS configuration is already present; preserving it."
        else
            echo "ERROR: ${DIREWOLF_CONFIG} already exists, but PCS_SETUP_APRS is not yes."
            echo "Refusing to stop or replace an untracked Dire Wolf installation."
            echo "Review the existing configuration and reconcile the PCS install state first."
            exit 1
        fi
    fi

    echo
    echo "=== Stage Dire Wolf / APRS Software ==="
    echo
    echo "Installing the Raspberry Pi OS / Debian Dire Wolf package..."
    sudo apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
        direwolf nftables gpiod alsa-utils git gcc g++ make cmake \
        libasound2-dev libudev-dev libavahi-client-dev libgpiod-dev libgps-dev
    ensure_supported_direwolf
    for service_group in audio dialout gpio; do
        if getent group "${service_group}" >/dev/null 2>&1; then
            sudo usermod -a -G "${service_group}" direwolf
        fi
    done

    echo "Installing the PCS safe-default configuration example..."
    sudo install -d -o root -g direwolf -m 0750 "${APRS_CONFIG_DIR}"
    sudo install -o root -g direwolf -m 0640 "${TEMPLATE_SRC}" "${TEMPLATE_DST}"

    if [[ "${was_active}" -eq 0 ]]; then
        echo "Keeping Dire Wolf stopped and disabled until hardware activation."
        sudo systemctl disable --now direwolf.service >/dev/null 2>&1 || true
        set_install_config_value "PCS_SETUP_APRS" "staged"
        PCS_SETUP_APRS="staged"
    fi

    show_check

    echo
    echo "Dire Wolf software staging complete."
    echo "No callsign, APRS-IS passcode, audio device, PTT method, beacon, or RF TX was configured."
    echo "See docs/direwolf-aprs.md before hardware activation."
}

case "${MODE}" in
    --prepare)
        prepare
        ;;
    --check)
        show_check
        ;;
    --capabilities)
        show_capabilities
        ;;
    --configure-options)
        configure_options
        ;;
    --import-commissioned-profile)
        replace_with_commissioned_profile
        ;;
    --prepare-uart)
        prepare_uart
        ;;
    --record-validation)
        record_validation
        ;;
    --list-audio)
        list_audio
        ;;
    --detect-audio)
        detect_audio
        ;;
    --set-rx-level)
        set_rx_level "${PROFILE}"
        ;;
    --set-tx-timing)
        set_tx_timing "${PROFILE}" "${VALUE2}"
        ;;
    --software-test)
        bash "${SOFTWARE_TEST}"
        ;;
    --render-config)
        render_config "${PROFILE}"
        ;;
    --validate-config)
        validate_config_command "${PROFILE}"
        ;;
    --activate-rx)
        activate_profile rx
        ;;
    --activate-tx)
        activate_profile tx
        ;;
    --rollback)
        rollback_config
        ;;
    -h|--help)
        usage
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
