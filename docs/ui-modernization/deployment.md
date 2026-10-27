# UI deployment and rollback

Obtain current explicit approval before executing on PCS. The operator authorized
the deployment recorded in [live-deployment.md](live-deployment.md). Publish/review the exact branch commit and
complete GitHub CI before appliance activation. The validated baseline is
v2.2.2 (`ba80c8d`); recheck newer main/live changes and preserve local work before
deploying. Do not overwrite a dirty appliance checkout or substitute a baseline.

This scoped procedure uses the existing allowlisted static stager and atomically
switches its versioned site. It explicitly restarts the Python web backend.
The legacy port-8080 redirect also restarts through its existing
`Requires=pcs-control-panel.service` dependency. Treat both as the web restart
scope when comparing service processes; verify every other service is unchanged. nginx
configuration, units, snapshots, credentials, privileged helpers, VLANs, WAN,
power monitoring and radio remain in place. The normal complete installer is
still supported, but is unnecessary for a commissioned stack receiving only
these presentation changes.

## Preparation after approval

Use the physical appliance's verified SSH identity or its local terminal.
Verify the actual `pcs-control-panel.service` ExecStart points at the checkout
below and `/var/www/pcs` is the managed site symlink. Verify nginx and backend
are already active; stop if the layout differs. Set `ui_commit` to the full
reviewed candidate SHA fetched from the PR or transferred in a verified Git
bundle. The development branch alone is not evidence of appliance acceptance.

```sh
repo=/home/pi/Projects/PCS-Portable-Comm-Server
cd "$repo"
git status --short
systemctl show pcs-control-panel.service -p ExecStart
systemctl is-active pcs-control-panel.service nginx.service
readlink -f /var/www/pcs
sudo nginx -t
```

Before switching, require a clean source tree and preserve the original source
and static pointer. Keep the resulting backup path for rollback.

```sh
test -z "$(git status --porcelain)" || { echo 'Preserve local work first'; exit 1; }
: "${ui_commit:?Set ui_commit to the full reviewed candidate SHA}"
test -L /var/www/pcs || exit 1
before_commit=$(git rev-parse HEAD)
before_static=$(readlink -f /var/www/pcs)
test -d "$before_static" || exit 1
ui_backup=$(sudo mktemp -d /var/backups/pcs-ui-before.XXXXXX)
printf '%s\n' "$before_commit" | sudo tee "$ui_backup/source.commit" >/dev/null
printf '%s\n' "$before_static" | sudo tee "$ui_backup/static.path" >/dev/null
git archive "$before_commit" | sudo tee "$ui_backup/source.tar" >/dev/null
printf 'Rollback directory: %s\n' "$ui_backup"
git cat-file -e "$ui_commit^{commit}" || exit 1
git switch --detach "$ui_commit"
```

Stage only the existing public allowlist and activate its symlink. This imports
the helper without calling its broader migration routine.

```sh
sudo python3 - "$repo" <<'PY'
import importlib.util
from pathlib import Path
import sys
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('frontend', root / 'scripts/pcs_frontend_install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
release = module.stage_site()
module.activate_site(release)
print('Activated static release:', release)
PY
sudo systemctl restart pcs-control-panel.service
sudo nginx -t
curl -fsS http://127.0.0.1/health
curl -fsS http://127.0.0.1/api/public-status >/dev/null
curl -I http://127.0.0.1/admin/
curl -I http://127.0.0.1:8080/
./scripts/pcs-self-test.sh
```

Expect admin 303 to login, legacy 308 to `/admin/`, fresh public snapshots and
normal appliance health. Verify public and authenticated pages from the LAN,
including redaction, selected sections, real measurements, settings accessibility,
current faults/offline labeling and staged controls. A backend restart invalidates
in-memory sessions. Do not exercise disruptive actions just to test the UI.
If any gate fails, roll back; retain both old and new static releases.

## Exact UI rollback after approval

Set `ui_backup` to the recorded `/var/backups/pcs-ui-before.XXXXXX` directory;
do not choose an unrelated historical frontend migration backup. Require that
the source tree has no new changes before switching back. No credentials or
configuration were changed by the scoped procedure.

```sh
repo=/home/pi/Projects/PCS-Portable-Comm-Server
cd "$repo"
: "${ui_backup:?Set ui_backup to the recorded UI backup directory}"
test -z "$(git status --porcelain)" || { echo 'Preserve new local work first'; exit 1; }
before_commit=$(sudo cat "$ui_backup/source.commit")
before_static=$(sudo cat "$ui_backup/static.path")
test -d "$before_static" || exit 1
git switch --detach "$before_commit"
sudo python3 - "$repo" "$before_static" <<'PY'
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location('frontend', Path(sys.argv[1]) / 'scripts/pcs_frontend_install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.activate_site(Path(sys.argv[2]))
PY
sudo nginx -t
sudo systemctl restart pcs-control-panel.service
curl -fsS http://127.0.0.1/health
curl -fsS http://127.0.0.1/api/public-status >/dev/null
curl -I http://127.0.0.1/admin/
```

The preserved source tar is an additional recovery artifact, not a reason to
extract over an unexamined working tree. For a full installer migration instead
of this scoped procedure, use its newly reported `migration-*` manifests and
the existing `docs/pcs-web-frontend.md` recovery instructions; never mix the two
rollback procedures.
