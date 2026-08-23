#!/usr/bin/env bash

set -Eeuo pipefail

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT

require_tool() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: required Dire Wolf test tool is missing: $1" >&2
        exit 1
    fi
}

decode_fixture() {
    local label="$1"
    local wav_file="$2"
    local output_file="$3"

    atest "${wav_file}" >"${output_file}" 2>&1
    if ! grep -Fq "W8IJC-10>APPCS" "${output_file}"; then
        echo "ERROR: ${label} fixture did not decode the expected frame." >&2
        sed -n '1,120p' "${output_file}" >&2
        exit 1
    fi
    if ! grep -Eq '[1-9][0-9]* packets? decoded in' "${output_file}"; then
        echo "ERROR: ${label} fixture reported no decoded packets." >&2
        exit 1
    fi
    echo "PASS: ${label} encode/decode loopback"
}

require_tool gen_packets
require_tool atest

GEN_PACKETS_HELP="$(gen_packets -h 2>&1 || true)"

PACKET='W8IJC-10>APPCS:>PCS synthetic software validation'

printf '%s\n' "${PACKET}" \
    | gen_packets -a 25 -o "${TEMP_DIR}/ax25.wav" - >/dev/null
decode_fixture "AX.25" "${TEMP_DIR}/ax25.wav" "${TEMP_DIR}/ax25.txt"

if grep -Eq -- '-X([[:space:]]+n)?' <<<"${GEN_PACKETS_HELP}"; then
    printf '%s\n' "${PACKET}" \
        | gen_packets -a 25 -X 1 -o "${TEMP_DIR}/fx25.wav" - >/dev/null
    decode_fixture "FX.25" "${TEMP_DIR}/fx25.wav" "${TEMP_DIR}/fx25.txt"
else
    echo "ERROR: installed gen_packets does not advertise FX.25 transmit support." >&2
    exit 1
fi

if grep -Eq -- '-v' <<<"${GEN_PACKETS_HELP}"; then
    printf '%s\n' "${PACKET}" \
        | gen_packets -a 25 -v 1,1 -o "${TEMP_DIR}/timing.wav" - >/dev/null
    decode_fixture "variable-speed AX.25" "${TEMP_DIR}/timing.wav" "${TEMP_DIR}/timing.txt"
else
    echo "SKIP: installed gen_packets has no variable-speed fixture option"
fi

echo "Dire Wolf synthetic packet validation passed without using audio or RF hardware."
