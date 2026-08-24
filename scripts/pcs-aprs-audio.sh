#!/usr/bin/env bash

set -Eeuo pipefail

CONFIG_FILE="${PCS_APRS_AUDIO_CONFIG:-/etc/pcs/aprs/audio.conf}"
WAIT_SECONDS=0
MODE="--apply"

if [[ -r "${CONFIG_FILE}" ]]; then
    # shellcheck source=/dev/null
    source "${CONFIG_FILE}"
fi

PCS_APRS_AUDIO_CARD="${PCS_APRS_AUDIO_CARD:-Device}"
PCS_APRS_PLAYBACK_CONTROL="${PCS_APRS_PLAYBACK_CONTROL:-Speaker}"
PCS_APRS_PLAYBACK_LEVEL="${PCS_APRS_PLAYBACK_LEVEL:--16dB}"
PCS_APRS_CAPTURE_CONTROL="${PCS_APRS_CAPTURE_CONTROL:-Mic}"
PCS_APRS_CAPTURE_LEVEL="${PCS_APRS_CAPTURE_LEVEL:-69%}"
PCS_APRS_AGC_CONTROL="${PCS_APRS_AGC_CONTROL:-Auto Gain Control}"
PCS_APRS_AGC_STATE="${PCS_APRS_AGC_STATE:-off}"

usage() {
    cat <<'EOF'
Usage: pcs-aprs-audio [--apply|--check] [--wait-seconds N]

Apply or verify the commissioned PCS APRS USB sound-card mixer profile.
EOF
}

while (( $# > 0 )); do
    case "$1" in
        --apply|--check)
            MODE="$1"
            ;;
        --wait-seconds)
            shift
            [[ $# -gt 0 ]] || { echo "ERROR: --wait-seconds requires a value" >&2; exit 2; }
            WAIT_SECONDS="$1"
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
    shift
done

validate_value() {
    local name="$1"
    local value="$2"
    local pattern="$3"
    if [[ ! "${value}" =~ ${pattern} ]]; then
        echo "ERROR: ${name} contains an unsupported value: ${value}" >&2
        exit 2
    fi
}

validate_value PCS_APRS_AUDIO_CARD "${PCS_APRS_AUDIO_CARD}" '^[A-Za-z0-9_.-]{1,64}$'
validate_value PCS_APRS_PLAYBACK_CONTROL "${PCS_APRS_PLAYBACK_CONTROL}" '^[A-Za-z0-9 _./+-]{1,80}$'
validate_value PCS_APRS_PLAYBACK_LEVEL "${PCS_APRS_PLAYBACK_LEVEL}" '^-[0-9]{1,3}dB$|^[0-9]{1,3}%$'
validate_value PCS_APRS_CAPTURE_CONTROL "${PCS_APRS_CAPTURE_CONTROL}" '^[A-Za-z0-9 _./+-]{1,80}$'
validate_value PCS_APRS_CAPTURE_LEVEL "${PCS_APRS_CAPTURE_LEVEL}" '^[0-9]{1,3}%$'
validate_value PCS_APRS_AGC_CONTROL "${PCS_APRS_AGC_CONTROL}" '^[A-Za-z0-9 _./+-]{1,80}$'
validate_value PCS_APRS_AGC_STATE "${PCS_APRS_AGC_STATE}" '^(on|off)$'
validate_value WAIT_SECONDS "${WAIT_SECONDS}" '^[0-9]{1,3}$'

command -v amixer >/dev/null 2>&1 || {
    echo "ERROR: amixer is required to prepare the PCS APRS audio path." >&2
    exit 1
}

card_present() {
    amixer -c "${PCS_APRS_AUDIO_CARD}" info >/dev/null 2>&1
}

deadline=$((SECONDS + WAIT_SECONDS))
while ! card_present; do
    if (( SECONDS >= deadline )); then
        echo "ERROR: ALSA card ${PCS_APRS_AUDIO_CARD} is unavailable after ${WAIT_SECONDS}s." >&2
        exit 1
    fi
    sleep 1
done

control_text() {
    amixer -c "${PCS_APRS_AUDIO_CARD}" sget "$1"
}

if [[ "${MODE}" == "--apply" ]]; then
    amixer -q -c "${PCS_APRS_AUDIO_CARD}" sset "${PCS_APRS_PLAYBACK_CONTROL}" -- \
        "${PCS_APRS_PLAYBACK_LEVEL}" unmute
    amixer -q -c "${PCS_APRS_AUDIO_CARD}" sset "${PCS_APRS_CAPTURE_CONTROL}" \
        "${PCS_APRS_CAPTURE_LEVEL}" cap
    amixer -q -c "${PCS_APRS_AUDIO_CARD}" sset "${PCS_APRS_AGC_CONTROL}" \
        "${PCS_APRS_AGC_STATE}"
fi

playback="$(control_text "${PCS_APRS_PLAYBACK_CONTROL}")"
capture="$(control_text "${PCS_APRS_CAPTURE_CONTROL}")"
agc="$(control_text "${PCS_APRS_AGC_CONTROL}")"

if [[ "${PCS_APRS_PLAYBACK_LEVEL}" == *dB ]]; then
    playback_db="${PCS_APRS_PLAYBACK_LEVEL%dB}"
    grep -Eq "\\[${playback_db}([.]0+)?dB\\]" <<<"${playback}" || {
        echo "ERROR: playback control is not at ${PCS_APRS_PLAYBACK_LEVEL}." >&2
        exit 1
    }
else
    grep -Fq "[${PCS_APRS_PLAYBACK_LEVEL}]" <<<"${playback}" || {
        echo "ERROR: playback control is not at ${PCS_APRS_PLAYBACK_LEVEL}." >&2
        exit 1
    }
fi
grep -Fq '[on]' <<<"${playback}" || {
    echo "ERROR: playback control is muted." >&2
    exit 1
}
grep -Fq "[${PCS_APRS_CAPTURE_LEVEL}]" <<<"${capture}" || {
    echo "ERROR: capture control is not at ${PCS_APRS_CAPTURE_LEVEL}." >&2
    exit 1
}
grep -Fq '[on]' <<<"${capture}" || {
    echo "ERROR: capture control is not enabled." >&2
    exit 1
}

if [[ "${PCS_APRS_AGC_STATE}" == "off" ]]; then
    grep -Fq '[off]' <<<"${agc}" || {
        echo "ERROR: automatic gain control is not off." >&2
        exit 1
    }
else
    grep -Fq '[on]' <<<"${agc}" || {
        echo "ERROR: automatic gain control is not on." >&2
        exit 1
    }
fi

echo "PCS APRS audio ready: card ${PCS_APRS_AUDIO_CARD}, playback ${PCS_APRS_PLAYBACK_LEVEL}, capture ${PCS_APRS_CAPTURE_LEVEL}, AGC ${PCS_APRS_AGC_STATE}."
