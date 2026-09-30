#!/usr/bin/env bash
# Explicit opt-in installation; does not change the base installer or PCS services.
set -euo pipefail
umask 077
mode=${1:---check}
[[ $# -le 1 && "$mode" =~ ^--(install|check|remove)$ ]] || { echo 'Usage: setup-pcs-qualify.sh --install|--check|--remove' >&2; exit 2; }
[[ $EUID -eq 0 && -d /run/systemd/system ]] || { echo 'Root and systemd required' >&2; exit 1; }
source_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
runtime=/run/pcs-qualification
state=/var/lib/pcs-qualification
unit=/etc/systemd/system/pcs-qualify-cleanup.service
wrapper=/usr/local/sbin/pcs-qualify
modules=(pcs_qualify.py pcs_qualify_state.py pcs_qualify_observe.py pcs_qualify_safety.py pcs_qualify_witness.py pcs_qualify_wan.py pcs_qualify_fault.py pcs_qualify_lan.py pcs_qualify_scenario.py pcs_qualify_rf.py)
files=("$wrapper" "$unit")
for module in "${modules[@]}"; do files+=("/usr/local/lib/pcs/$module"); done
for dir in "$runtime" "$state"; do
    [[ ! -L $dir ]] || { echo 'Unsafe qualification directory' >&2; exit 1; }
    if [[ -e $dir ]]; then
        [[ $(stat -c '%u:%a' "$dir") == '0:700' ]] || { echo 'Unsafe qualification permissions' >&2; exit 1; }
    else
        install -d -m 0700 "$dir"
    fi
done
[[ ! -L $runtime/campaign.lock ]] || exit 1
exec 9>"$runtime/campaign.lock"
flock -n 9 || { echo 'Qualification campaign active' >&2; exit 1; }
check_files() {
    [[ -f $state/install.sha256 && ! -L $state/install.sha256 ]] || return 1
    sha256sum --status -c "$state/install.sha256"
}
# Upgrading adds modules. An old manifest must never authorize overwriting or
# removing a pre-existing new target which that manifest did not own.
if [[ -f $state/install.sha256 ]]; then
    for file in "${files[@]}"; do
        if [[ -e $file ]] && ! cut -c 67- "$state/install.sha256" | grep -Fxq -- "$file"; then
            echo 'Unowned qualification target collision' >&2
            exit 1
        fi
    done
fi
if [[ $mode == --check ]]; then
    check_files
    [[ $(stat -c '%u:%a' "$state/sessions") == '0:700' ]]
    systemctl is-enabled --quiet pcs-qualify-cleanup.service
    systemd-analyze verify "$unit"
    "$wrapper" list
    echo 'Qualification installation verified'
    exit 0
fi
if [[ $mode == --remove ]]; then
    if [[ ! -e $state/install.sha256 ]]; then
        for file in "${files[@]}"; do [[ ! -e $file ]] || { echo 'Unowned installation collision' >&2; exit 1; }; done
        echo 'Qualification already removed; evidence retained'
        exit 0
    fi
    check_files || { echo 'Installation changed; inspect before removal' >&2; exit 1; }
    "$wrapper" cleanup
    systemctl stop pcs-qualify-expiry.timer 2>/dev/null || true
    systemctl stop pcs-qualify-expiry.service 2>/dev/null || true
    systemctl disable --now pcs-qualify-cleanup.service
    for file in "${files[@]}"; do rm -f -- "$file"; done
    rm -f -- "$state/install.sha256"
    systemctl daemon-reload
    echo 'Qualification removed; private session evidence retained in /var/lib/pcs-qualification/sessions'
    exit 0
fi
for executable in python3 systemd-run systemctl systemd-analyze flock sha256sum ip; do command -v "$executable" >/dev/null; done
for file in "${files[@]}"; do [[ ! -L $file ]] || { echo 'Unsafe installation target' >&2; exit 1; }; done
if [[ -e $state/install.sha256 ]]; then
    check_files || { echo 'Installed files changed; inspect before upgrading' >&2; exit 1; }
    "$wrapper" cleanup
    systemctl stop pcs-qualify-expiry.timer 2>/dev/null || true
    systemctl stop pcs-qualify-expiry.service 2>/dev/null || true
else
    for file in "${files[@]}"; do [[ ! -e $file ]] || { echo 'Unowned installation collision' >&2; exit 1; }; done
fi
stage=$(mktemp -d "$state/.install-XXXXXXXX")
installed=0
was_enabled=0
systemctl is-enabled --quiet pcs-qualify-cleanup.service 2>/dev/null && was_enabled=1
rollback() {
    status=$?
    if [[ $installed == 0 ]]; then
        for index in "${!files[@]}"; do
            if [[ -f $stage/old-$index ]]; then cp -p -- "$stage/old-$index" "${files[$index]}";
            elif [[ -f $stage/absent-$index ]]; then rm -f -- "${files[$index]}"; fi
        done
        if [[ -f $stage/old-manifest ]]; then cp -p "$stage/old-manifest" "$state/install.sha256";
        else rm -f "$state/install.sha256"; fi
        if [[ $was_enabled == 0 ]]; then systemctl disable pcs-qualify-cleanup.service 2>/dev/null || true; fi
        systemctl daemon-reload || true
    fi
    # stage is created by mktemp under the fixed private state directory.
    rm -rf -- "$stage"
    exit "$status"
}
trap rollback EXIT
for index in "${!files[@]}"; do
    if [[ -e ${files[$index]} ]]; then cp -p -- "${files[$index]}" "$stage/old-$index";
    else touch "$stage/absent-$index"; fi
done
[[ ! -f $state/install.sha256 ]] || cp -p "$state/install.sha256" "$stage/old-manifest"
install -d -m 0755 /usr/local/lib/pcs /usr/local/sbin
[[ ! -L $state/sessions ]] || { echo 'Unsafe sessions target' >&2; exit 1; }
install -d -m 0700 "$state/sessions"
for module in "${modules[@]}"; do install -m 0644 "$source_root/scripts/$module" "/usr/local/lib/pcs/$module"; done
# Isolated Python mode excludes the script directory; add only the root-owned PCS module directory.
printf '%s\n' '#!/bin/sh' 'exec /usr/bin/python3 -I -c '\''import sys; sys.path.insert(0,"/usr/local/lib/pcs"); from pcs_qualify import main; raise SystemExit(main())'\'' "$@"' > "$stage/wrapper"
install -m 0755 "$stage/wrapper" "$wrapper"
install -m 0644 "$source_root/systemd/pcs-qualify-cleanup.service" "$unit"
systemd-analyze verify "$unit"
systemctl daemon-reload
systemctl enable pcs-qualify-cleanup.service
# Do not start while holding campaign.lock; boot-cleanup uses that same lock.
sha256sum "${files[@]}" > "$state/install.sha256"
"$wrapper" list >/dev/null
installed=1
echo 'Qualification installed; run pcs-qualify preflight before observation'
