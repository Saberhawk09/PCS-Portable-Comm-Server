#!/usr/bin/env bash

set -Eeuo pipefail

MODE="apply"
case "${1:-}" in
    ""|--apply)
        MODE="apply"
        ;;
    --check)
        MODE="check"
        ;;
    *)
        echo "Usage: $0 [--apply|--check]"
        exit 2
        ;;
esac

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Run this script as the normal PCS Pi user, not with sudo."
    echo "The script uses sudo only for the root-owned shutdown key and installed files."
    exit 1
fi

PISTAR_HOST="${PCS_PISTAR_HOST:-10.42.0.3}"
PISTAR_USER="${PCS_PISTAR_USER:-pi-star}"
PAIR_DIR="${PCS_PISTAR_PAIR_DIR:-/etc/pcs/pistar-shutdown}"
PRIVATE_KEY="${PAIR_DIR}/id_ed25519"
PUBLIC_KEY="${PRIVATE_KEY}.pub"
KNOWN_HOSTS="${PAIR_DIR}/known_hosts"
REMOTE_HELPER="/usr/local/sbin/pcs-remote-power"
REMOTE_SUDOERS="/etc/sudoers.d/pcs-remote-power"
PAIR_TARGET="${PISTAR_USER}@${PISTAR_HOST}"

TEMP_FILES=()

make_temp_file() {
    local path
    path="$(mktemp)"
    TEMP_FILES+=("${path}")
    echo "${path}"
}

cleanup() {
    if [[ "${#TEMP_FILES[@]}" -gt 0 ]]; then
        rm -f -- "${TEMP_FILES[@]}"
    fi
}

trap cleanup EXIT

for command_name in ssh ssh-keygen base64 python3; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "ERROR: Required command not found: ${command_name}"
        exit 1
    fi
done

pair_check() {
    sudo ssh \
        -i "${PRIVATE_KEY}" \
        -o BatchMode=yes \
        -o ConnectTimeout=8 \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="${KNOWN_HOSTS}" \
        "${PAIR_TARGET}" \
        check
}

if [[ "${MODE}" == "check" ]]; then
    if ! sudo test -r "${PRIVATE_KEY}" || ! sudo test -r "${KNOWN_HOSTS}"; then
        echo "ERROR: Pi-Star coordinated shutdown is not paired on PCS."
        exit 1
    fi

    check_output="$(pair_check)"
    if [[ "${check_output}" != *"PCS_PISTAR_REMOTE_POWER_READY"* ]]; then
        echo "ERROR: Pi-Star returned an unexpected readiness response."
        exit 1
    fi

    echo "Pi-Star coordinated shutdown pairing is ready."
    exit 0
fi

if sudo test -r "${PRIVATE_KEY}" && sudo test -r "${KNOWN_HOSTS}"; then
    set +e
    existing_check_output="$(pair_check 2>/dev/null)"
    existing_check_rc=$?
    set -e

    if [[ "${existing_check_rc}" -eq 0
        && "${existing_check_output}" == *"PCS_PISTAR_REMOTE_POWER_READY"* ]]; then
        echo "Pi-Star coordinated shutdown pairing is already ready."
        exit 0
    fi
fi

if [[ ! -t 0 ]]; then
    echo "ERROR: Password-assisted Pi-Star pairing requires an interactive terminal."
    echo "Run this script directly from a PCS Pi terminal."
    exit 1
fi

echo
echo "=== PCS / Pi-Star Coordinated Shutdown Pairing ==="
echo
echo "This one-time step will:"
echo "  - generate a dedicated PCS shutdown key"
echo "  - ask SSH for the Pi-Star password"
echo "  - restrict the installed key to readiness-check and poweroff commands"
echo "  - avoid saving the Pi-Star password"
echo
echo "Target: ${PAIR_TARGET}"
echo

if [[ "${PCS_PISTAR_PAIR_CONFIRM:-}" == "yes" ]]; then
    answer="yes"
    echo "Continue with password-assisted pairing? [Y/N] yes"
else
    read -r -p "Continue with password-assisted pairing? [Y/N] " answer
fi

case "${answer}" in
    y|Y|yes|YES|Yes)
        ;;
    *)
        echo "Pairing skipped."
        exit 0
        ;;
esac

sudo -v

echo
echo "--- Preparing the dedicated root-owned PCS key ---"
sudo install -d -o root -g root -m 0700 "${PAIR_DIR}"

if [[ ! -f "${PRIVATE_KEY}" ]]; then
    sudo ssh-keygen \
        -q \
        -t ed25519 \
        -N "" \
        -C "pcs-pistar-shutdown" \
        -f "${PRIVATE_KEY}"
    echo "Created ${PRIVATE_KEY}"
else
    echo "Reusing existing dedicated key: ${PRIVATE_KEY}"
fi

sudo chown root:root "${PRIVATE_KEY}" "${PUBLIC_KEY}"
sudo chmod 0600 "${PRIVATE_KEY}"
sudo chmod 0644 "${PUBLIC_KEY}"

public_key="$(sudo cat "${PUBLIC_KEY}")"
public_key_b64="$(printf '%s\n' "${public_key}" | base64 | tr -d '\r\n')"

bootstrap_script="$(make_temp_file)"
cat > "${bootstrap_script}" <<'REMOTE'
#!/usr/bin/env bash
set -Eeuo pipefail

public_key_b64="$1"
restore_ro=0

remount_read_only() {
    local attempt
    for attempt in $(seq 1 30); do
        sync
        if sudo mount -o remount,ro / 2>/dev/null; then
            return 0
        fi
        sleep 2
    done
    return 1
}

restore_root() {
    if [[ "${restore_ro}" -eq 1 ]]; then
        remount_read_only || true
    fi
}

trap restore_root EXIT

root_options="$(findmnt -no OPTIONS /)"
if [[ ",${root_options}," == *,ro,* ]]; then
    sudo mount -o remount,rw /
    restore_ro=1
fi

umask 077
mkdir -p "${HOME}/.ssh"
touch "${HOME}/.ssh/authorized_keys"
chmod 0700 "${HOME}/.ssh"
chmod 0600 "${HOME}/.ssh/authorized_keys"

python3 - "${HOME}/.ssh/authorized_keys" "${public_key_b64}" <<'PY'
from base64 import b64decode
from pathlib import Path
import sys

path = Path(sys.argv[1])
public_key = b64decode(sys.argv[2]).decode("utf-8").strip()
parts = public_key.split()
if len(parts) < 2:
    raise SystemExit("ERROR: Invalid PCS public key")

blob = parts[1]
lines = path.read_text().splitlines()
if not any(blob in line.split() for line in lines):
    lines.append(public_key)
path.write_text("\n".join(lines) + "\n")
PY

sync

if [[ "${restore_ro}" -eq 1 ]]; then
    if remount_read_only; then
        restore_ro=0
    else
        echo "ERROR: Pi-Star root filesystem did not return to read-only." >&2
        exit 1
    fi
fi
REMOTE
chmod 0700 "${bootstrap_script}"
bootstrap_b64="$(base64 < "${bootstrap_script}" | tr -d '\r\n')"

echo
echo "--- Installing the dedicated key on Pi-Star ---"
echo "SSH will now ask for the Pi-Star password."
echo "The password is handled by SSH and is not read or saved by this script."
echo

ssh \
    -tt \
    -o PreferredAuthentications=password,keyboard-interactive \
    -o PubkeyAuthentication=no \
    -o NumberOfPasswordPrompts=3 \
    -o ConnectTimeout=10 \
    -o StrictHostKeyChecking=accept-new \
    "${PAIR_TARGET}" \
    "echo ${bootstrap_b64} | base64 -d | bash -s -- ${public_key_b64}"

user_known_hosts="${HOME}/.ssh/known_hosts"
known_host_lines=""
if [[ -f "${user_known_hosts}" ]]; then
    known_host_lines="$(ssh-keygen -F "${PISTAR_HOST}" -f "${user_known_hosts}" 2>/dev/null \
        | grep -v '^#' || true)"
fi

if [[ -z "${known_host_lines}" ]]; then
    echo "ERROR: The accepted Pi-Star host key was not found in ${user_known_hosts}."
    echo "The shutdown key was copied, but pairing cannot continue safely."
    exit 1
fi

printf '%s\n' "${known_host_lines}" \
    | sudo tee "${KNOWN_HOSTS}" >/dev/null
sudo chown root:root "${KNOWN_HOSTS}"
sudo chmod 0600 "${KNOWN_HOSTS}"

provision_script="$(make_temp_file)"
cat > "${provision_script}" <<'REMOTE'
#!/usr/bin/env bash
set -Eeuo pipefail

public_key_b64="$1"
remote_helper="$2"
remote_sudoers="$3"
restore_ro=0

remount_read_only() {
    local attempt
    for attempt in $(seq 1 30); do
        sync
        if sudo mount -o remount,ro / 2>/dev/null; then
            return 0
        fi
        sleep 2
    done
    return 1
}

cleanup() {
    if [[ "${restore_ro}" -eq 1 ]]; then
        remount_read_only || true
    fi
}

trap cleanup EXIT

root_options="$(findmnt -no OPTIONS /)"
if [[ ",${root_options}," == *,ro,* ]]; then
    sudo mount -o remount,rw /
    restore_ro=1
fi

systemctl_bin="$(command -v systemctl)"
remote_user="$(id -un)"
helper_temp="$(mktemp)"
sudoers_temp="$(mktemp)"
trap 'rm -f "${helper_temp}" "${sudoers_temp}"; cleanup' EXIT

cat > "${helper_temp}" <<EOF
#!/usr/bin/env bash
set -u

case "\${SSH_ORIGINAL_COMMAND:-}" in
    check)
        echo "PCS_PISTAR_REMOTE_POWER_READY"
        ;;
    poweroff)
        echo "PCS_PISTAR_POWEROFF_ACCEPTED"
        sudo ${systemctl_bin} --no-block poweroff
        ;;
    *)
        echo "ERROR: This key permits only check or poweroff." >&2
        exit 2
        ;;
esac
EOF

cat > "${sudoers_temp}" <<EOF
# Allow only the restricted PCS remote-power helper to power off Pi-Star.
${remote_user} ALL=(root) NOPASSWD: ${systemctl_bin} --no-block poweroff
EOF

sudo install -o root -g root -m 0755 "${helper_temp}" "${remote_helper}"
sudo install -o root -g root -m 0440 "${sudoers_temp}" "${remote_sudoers}"
sudo visudo -cf "${remote_sudoers}"

python3 - "${HOME}/.ssh/authorized_keys" "${public_key_b64}" "${remote_helper}" <<'PY'
from base64 import b64decode
from pathlib import Path
import sys

path = Path(sys.argv[1])
public_key = b64decode(sys.argv[2]).decode("utf-8").strip()
remote_helper = sys.argv[3]
parts = public_key.split()
if len(parts) < 2:
    raise SystemExit("ERROR: Invalid PCS public key")

key_type, blob = parts[:2]
restricted = (
    f'restrict,command="{remote_helper}" '
    f"{key_type} {blob} pcs-pistar-shutdown"
)

lines = path.read_text().splitlines()
lines = [line for line in lines if blob not in line.split()]
lines.append(restricted)
path.write_text("\n".join(lines) + "\n")
PY

chmod 0600 "${HOME}/.ssh/authorized_keys"
sync

if [[ "${restore_ro}" -eq 1 ]]; then
    if remount_read_only; then
        restore_ro=0
    else
        echo "ERROR: Pi-Star root filesystem did not return to read-only." >&2
        exit 1
    fi
fi
REMOTE
chmod 0700 "${provision_script}"
provision_b64="$(base64 < "${provision_script}" | tr -d '\r\n')"

echo
echo "--- Restricting the key to Pi-Star remote power control ---"
sudo ssh \
    -i "${PRIVATE_KEY}" \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="${KNOWN_HOSTS}" \
    "${PAIR_TARGET}" \
    "echo ${provision_b64} | base64 -d | bash -s -- ${public_key_b64} ${REMOTE_HELPER} ${REMOTE_SUDOERS}"

echo
echo "--- Verifying restricted readiness check ---"
check_output="$(pair_check)"

if [[ "${check_output}" != *"PCS_PISTAR_REMOTE_POWER_READY"* ]]; then
    echo "ERROR: Pi-Star returned an unexpected readiness response."
    exit 1
fi

echo
echo "Pi-Star coordinated shutdown pairing complete."
echo
echo "The PCS shutdown button can now request Pi-Star poweroff first."
echo "The dedicated key cannot open a shell or run arbitrary commands."
