#!/bin/bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]]
stage=/tmp/pcs-dashboard-stage
repo=/home/pi/Projects/PCS-Portable-Comm-Server
for f in /usr/local/lib/pcs/pcs_dashboard_snapshot.py /etc/systemd/system/pcs-dashboard-snapshot.service; do
    [[ ! -e "$f" ]] || { echo 'Snapshot component already exists; stop for review.' >&2; exit 1; }
done
systemctl is-active --quiet pcs-control-panel.service
systemctl is-active --quiet pcs-stats-api.service
python3 -m py_compile "$stage/scripts/pcs_dashboard_snapshot.py" "$stage/web/pcs-control-panel/pcs_control_panel.py" "$stage/web/pcs-control-panel/pcs_stats_api.py"
bash -n "$stage/scripts/pcs-web-action.sh"
bash -n "$stage/scripts/pcs-self-test.sh"
backup=$(mktemp -d /var/backups/pcs-dashboard-before.XXXXXX)
chmod 700 "$backup"
tar -C / -cpf "$backup/files.tar" usr/local/sbin/pcs-web-action home/pi/Projects/PCS-Portable-Comm-Server/scripts/pcs-self-test.sh usr/local/lib/pcs-stats-api/pcs_stats_api.py home/pi/Projects/PCS-Portable-Comm-Server/web/pcs-control-panel/pcs_control_panel.py
cat > "$backup/rollback.sh" <<'ROLLBACK'
#!/bin/bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]]
backup=$(cd "$(dirname "$0")" && pwd)
systemctl disable --now pcs-dashboard-snapshot.service || true
tar -C / -xpf "$backup/files.tar"
rm -f /usr/local/lib/pcs/pcs_dashboard_snapshot.py /etc/systemd/system/pcs-dashboard-snapshot.service
systemctl daemon-reload
systemctl restart pcs-control-panel.service pcs-stats-api.service
ROLLBACK
chmod 700 "$backup/rollback.sh"
trap '"$backup/rollback.sh"' ERR
install -o root -g root -m 0644 "$stage/scripts/pcs_dashboard_snapshot.py" /usr/local/lib/pcs/pcs_dashboard_snapshot.py
install -o root -g root -m 0644 "$stage/systemd/pcs-dashboard-snapshot.service" /etc/systemd/system/pcs-dashboard-snapshot.service
install -o root -g root -m 0755 "$stage/scripts/pcs-web-action.sh" /usr/local/sbin/pcs-web-action
install -o pi -g pi -m 0755 "$stage/scripts/pcs-self-test.sh" "$repo/scripts/pcs-self-test.sh"
install -o root -g root -m 0755 "$stage/web/pcs-control-panel/pcs_stats_api.py" /usr/local/lib/pcs-stats-api/pcs_stats_api.py
install -o pi -g pi -m 0755 "$stage/web/pcs-control-panel/pcs_control_panel.py" "$repo/web/pcs-control-panel/pcs_control_panel.py"
systemctl daemon-reload
systemctl enable --now pcs-dashboard-snapshot.service
systemctl restart pcs-control-panel.service pcs-stats-api.service
systemctl is-active --quiet pcs-dashboard-snapshot.service
systemctl is-active --quiet pcs-control-panel.service
systemctl is-active --quiet pcs-stats-api.service
trap - ERR
printf 'Dashboard installed. Rollback: sudo %s/rollback.sh\n' "$backup"
