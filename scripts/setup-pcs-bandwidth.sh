#!/usr/bin/env bash
# Explicit opt-in, manifest-owned installation with rollback.
set -euo pipefail
umask 077
mode=${1:---check}
[[ $# -le 1 && "$mode" =~ ^--(install|check|remove)$ ]] || { echo 'Usage: setup-pcs-bandwidth.sh --install|--check|--remove'; exit 2; }
[[ $EUID == 0 ]] || { echo 'Run with sudo'; exit 1; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
helper=/usr/local/sbin/pcs-bandwidth
unit=/etc/systemd/system/pcs-bandwidth.service
sudoers=/etc/sudoers.d/pcs-bandwidth
state=/var/lib/pcs-bandwidth-install
manifest=$state/install.sha256
config=/etc/pcs/bandwidth.yaml
modules=(pcs_bandwidth.py pcs_bandwidth_config.py pcs_bandwidth_engine.py)
files=("$helper" "$unit" "$sudoers")
for module in "${modules[@]}"; do files+=("/usr/local/lib/pcs-bandwidth/$module"); done
for path in "$state" "$config" /usr/local/lib/pcs-bandwidth "${files[@]}"; do
    [[ ! -L $path ]] || { echo "Refusing symlink: $path"; exit 1; }
done
install -d -m 0700 "$state"
exec 9>"$state/install.lock"
flock -n 9 || { echo 'Another bandwidth installation is running'; exit 1; }
if [[ -f $manifest ]]; then
    sha256sum --status -c "$manifest" || { echo 'Installed files changed; inspect before upgrading/removing'; exit 1; }
    for file in "${files[@]}"; do
        if [[ -e $file ]] && ! cut -c 67- "$manifest" | grep -Fxq -- "$file"; then
            echo "Unowned installation target: $file"; exit 1
        fi
    done
else
    for file in "${files[@]}"; do
        [[ ! -e $file ]] || { echo "Unowned installation target: $file"; exit 1; }
    done
fi
if [[ $mode == --remove ]]; then
    if [[ ! -f $manifest ]]; then echo 'Already removed'; exit 0; fi
    systemctl disable --now pcs-bandwidth.service
    "$helper" stop
    rm -f -- "${files[@]}" "$manifest"
    systemctl daemon-reload
    echo 'Removed; /etc/pcs/bandwidth.yaml retained'
    exit 0
fi
for tool in python3 ip tc nft systemctl visudo; do command -v "$tool" >/dev/null; done
python3 -c 'import yaml'
if [[ $mode == --check ]]; then
    [[ -f $manifest ]] || { echo 'Not installed'; exit 1; }
    "$helper" validate
    systemd-analyze verify "$unit"
    visudo -cf "$sudoers"
    "$helper" status
    exit 0
fi
candidate_config=$config
[[ -f $candidate_config ]] || candidate_config=$root/config/bandwidth.example.yaml
python3 "$root/scripts/pcs_bandwidth.py" --config "$candidate_config" validate
stage=$(mktemp -d "$state/.install-XXXXXXXX")
was_active=0
was_enabled=0
config_created=0
systemctl is-active --quiet pcs-bandwidth.service && was_active=1
systemctl is-enabled --quiet pcs-bandwidth.service 2>/dev/null && was_enabled=1
for index in "${!files[@]}"; do
    [[ ! -e ${files[$index]} ]] || cp -p -- "${files[$index]}" "$stage/old-$index"
done
[[ ! -f $manifest ]] || cp -p "$manifest" "$stage/old-manifest"
rollback() {
    result=$?
    if [[ $result != 0 ]]; then
        systemctl stop pcs-bandwidth.service 2>/dev/null || true
        for index in "${!files[@]}"; do
            if [[ -f $stage/old-$index ]]; then cp -p -- "$stage/old-$index" "${files[$index]}";
            else rm -f -- "${files[$index]}"; fi
        done
        if [[ -f $stage/old-manifest ]]; then cp -p "$stage/old-manifest" "$manifest";
        else rm -f -- "$manifest"; fi
        [[ $config_created == 0 ]] || rm -f -- "$config"
        [[ $was_enabled == 1 ]] || systemctl disable pcs-bandwidth.service 2>/dev/null || true
        systemctl daemon-reload || true
        [[ $was_active == 0 ]] || systemctl start pcs-bandwidth.service || true
    fi
    rm -rf -- "$stage"
    exit "$result"
}
trap rollback EXIT
if [[ $was_active == 1 ]]; then systemctl stop pcs-bandwidth.service; fi
install -d -m 0755 /usr/local/lib/pcs-bandwidth
[[ -d /etc/pcs ]] || install -d -m 0755 /etc/pcs
for module in "${modules[@]}"; do
    install -m 0644 "$root/scripts/$module" "/usr/local/lib/pcs-bandwidth/$module"
done
cat > "$helper" <<'WRAPPER'
#!/bin/sh
exec /usr/bin/python3 /usr/local/lib/pcs-bandwidth/pcs_bandwidth.py "$@"
WRAPPER
chmod 0755 "$helper"
if [[ ! -f $config ]]; then
    install -m 0600 "$root/config/bandwidth.example.yaml" "$config"
    config_created=1
fi
install -m 0644 "$root/systemd/pcs-bandwidth.service" "$unit"
cat > "$stage/sudoers" <<'SUDOERS'
# Fixed commands only: bounded JSON input is validated by the root helper.
pi ALL=(root) NOPASSWD: /usr/local/sbin/pcs-bandwidth web-status
pi ALL=(root) NOPASSWD: /usr/local/sbin/pcs-bandwidth web-update
SUDOERS
visudo -cf "$stage/sudoers"
install -m 0440 "$stage/sudoers" "$sudoers"
systemd-analyze verify "$unit"
systemctl daemon-reload
systemctl enable --now pcs-bandwidth.service
sha256sum "${files[@]}" > "$manifest"
echo 'Installed. Fresh configuration is unrestricted. Select a profile explicitly.'
