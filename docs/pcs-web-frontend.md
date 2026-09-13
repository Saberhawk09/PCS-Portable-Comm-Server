# PCS web frontend: architecture and deployment

## Status

The nginx migration, static portal and mobile polish were deployed to PCS with
user authorization on 2026-09-12. Validation used an isolated Debian 13/Python
3.13 VM with fixture status/actions, isolated live staging, and production
endpoint and browser checks. Configuration and source rollback backups remain
on the appliance.

Review in three logical phases: serving architecture and backend isolation;
static presentation and graceful failure; then mobile polish, validation and
deployment documentation. Keep separate power-monitor and Stats API edits out
of frontend commits. The initial architecture-only tests retained Python's
homepage; the final configuration serves the static portal at `/`.

## Routes and security

| Route | Behavior |
| --- | --- |
| `/` | Static PCS service launcher |
| `/assets/` | Installed local CSS/JavaScript |
| `/files/`, `/docs/` | Static Samba instructions and field guide |
| `/radio/`, `/pistar/` | Static optional-service information |
| `/status/` | Existing Python-rendered public status view |
| `/api/public-status`, `/health` | Existing public-safe backend endpoints |
| `/admin`, `/admin/...` | Existing authenticated Python application |
| `/cockpit/` | Redirect to HTTPS on the requested host, port 9090 |
| `/openwrt/` | Redirect to the PCS access point at 10.42.0.2 |
| Other paths | 404 |

nginx owns TCP 80. Python binds `127.0.0.1:8081` without
`CAP_NET_BIND_SERVICE`. The separate `:8080` compatibility service redirects
to the requested host's port-80 `/admin/`. Static pages use relative links;
optional Pi-Star URLs come from the existing public configuration.

[pcs.conf](../config/nginx/pcs.conf) has no SPA fallback, generic localhost proxy,
directory listing or repository exposure. `proxy_pass` has no URI suffix,
preserving admin paths/queries as described in the
[nginx proxy documentation](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_pass).
Cookies, redirects and Host pass through. nginx overwrites forwarded client
headers. Python trusts one valid `X-Real-IP` only from loopback when
`PCS_CONTROL_TRUST_PROXY=1`, preserving per-client login/password throttling.

PBKDF2, sessions, CSRF, password handling and the fixed action dispatcher remain
in Python. The public schema is unchanged. Static content has a self-only
asset/connect CSP; proxied documents retain Python's existing nonce/style policy.
Assets revalidate after updates. Bodies are capped at 64 KiB, public proxy reads
at 90 seconds and admin reads at 420 seconds (300s action + 75s dashboard + 10s
settings lookup, with margin). Connections/body/send waits are bounded. nginx
does not cache or retry admin operations.

Avahi/DNS, Stats API TLS/Android trust, Samba and RF services are unchanged.
`10.42.0.1` always remains an entry point; `pcs.local` depends on existing mDNS
configuration and is not required.

## Installer transaction

After deployment authorization, run `./scripts/setup-pcs-control-panel.sh`.
Its existing credential/helper setup invokes
[pcs_frontend_install.py](../scripts/pcs_frontend_install.py) as root. The helper:

1. Installs nginx if needed, temporarily masking package service startup.
   Package-lock contention has bounded retries; other package failures surface.
2. Saves affected nginx files/symlinks, unit files, static link and service state
   in `/var/backups/pcs-frontend/migration-*`.
3. Runs standalone `nginx -t` before modifying installed configuration.
4. Stages allowlisted public files in `/var/www/pcs-releases/site-*`, atomically
   switches `/var/www/pcs`, installs the PCS vhost, disables the distribution
   default site, and validates the full installed nginx configuration.
5. Restarts the loopback backend and legacy redirect, validates nginx again,
   starts/reloads nginx, and checks pages, assets, JSON, login redirects/cookie,
   compatibility redirect, and listener addresses/process owners.

Failures restore prior files and site link. If service handoff began, rollback
stops the new stack and restores previous enabled/active states. Every nginx
activation, including rollback, follows validation. Unrelated nginx sites are
retained; conflicts fail validation. An unmanaged directory at `/var/www/pcs`
is rejected rather than overwritten. Existing systemd overrides remain in force;
unsafe resulting listeners fail endpoint validation.

Backups and old releases are retained. The transaction does not uninstall
packages or reverse earlier credential/helper setup. Restarting Python invalidates
in-memory sessions, requiring login again. Power loss/SIGKILL cannot roll back
in-process. If rollback fails, recover the affected files and service state using
the reported backup's `files.json`, numbered backups and `services.json`; validate
nginx before activation. Do not blindly repeat a failed migration.

Installation needs Debian packages or cached packages. Runtime local services
and the portal require no Internet connection.

## Validation evidence (2026-09-12)

- Debian 13/Python 3.13: **530 Python tests passed, no skips**, including real
  nginx authentication/proxy tests and Linux filesystem transaction tests.
- **9 JavaScript tests passed**: malformed/missing data, real zero vs unavailable
  power, optional services, URL validation, text-only alerts, stale readings and
  recovery after failed requests.
- Real systemd rehearsals passed: old Python-port-80 upgrade, repeat install,
  invalid-config preservation, post-handoff failure with full site/config/unit/
  service restoration, and fresh install from missing PCS units.
- The VM showed nginx on `0.0.0.0:80`, Python on `127.0.0.1:8081`, legacy redirect
  on `0.0.0.0:8080`; endpoint checks passed after rollback too.
- Browser inspection at 360px: no horizontal overflow and no link targets below
  44px after corrections. Desktop uses four service columns.
- Python compilation, changed-shell syntax and whitespace checks passed.
- Live PCS staging passed on Debian 13/Python 3.13.5 using an extracted nginx
  1.26.3 package, without installing packages or changing production services.
  The candidate backend and nginx listened only on loopback test ports. Real
  public status, static pages/assets, admin login routing and the existing
  legacy redirect passed. Browser inspection through an SSH tunnel confirmed
  actual appliance readings, phone/tablet layout and no browser errors.
- Stopping the staged backend left static pages/assets available and returned
  502 for its status API. Both staged servers were then stopped. Production
  control-panel, redirect, power-monitor and Stats API process IDs, start times
  and restart counts were unchanged; original production endpoints remained
  healthy.

The VM uses simulated data and fixed test actions, so no radio or PCS hardware
is operated. Production port 80 was migrated successfully on 2026-09-12.
The portal was verified from a browser on the home LAN at `192.168.50.236`;
Python port 8081 was inaccessible from that client. Appliance-local checks of
`10.42.0.1` passed, but this workstation has no route to that subnet and
`pcs.local` did not resolve. Those two client-side checks remain unverified. Authenticated action,
password and CSRF regression tests ran against isolated fixtures, not live
privileged actions.

Pre-release production self-test passed every service category, with one warning
for the then-uncommitted frontend changes. nginx, control-panel and legacy
redirect are enabled. Credential/session-key/dispatcher hashes and unrelated
power-monitor, Stats API and Dire Wolf service states were unchanged. Backups:
`/var/backups/pcs-frontend/migration-t0m7p18o` (configuration/services) and
`/var/backups/pcs-frontend/pcs-web-deploy-20260912` (source files, deployed
manifest and self-test log). These backups describe the initial deployment;
the v1.9 release follows the repository release checklist.
Python 3.13 matches existing CI; Debian 12's Python 3.11 cannot parse syntax
already present in the backend.

## Appliance validation and deployment

Before deployment, verify Python, free space, overrides and current port owners;
preserve the working checkout and `/etc` configuration. Do not overwrite
concurrent power or Stats API changes. After the installer succeeds:

```bash
sudo nginx -t
curl -fsS http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1/
curl -fsS http://127.0.0.1/api/public-status
curl -I http://127.0.0.1/admin/
curl -I http://127.0.0.1:8080/
sudo ss -ltnp
./scripts/pcs-self-test.sh
```

Expect admin `303` to `/admin/login`, legacy `308` to port-80 `/admin/`, nginx
alone on port 80 and no LAN-reachable Python backend. Verify homepage, assets,
optional services and authenticated administration from a LAN browser. Check
`pcs.local` only if configured. Avoid shutdown, reboot, password rotation or RF
actions merely to smoke-test routing on the working appliance.

## Future services

Register one exact path, fixed upstream/static directory, access policy, limits
and health check per service. Test URI/query handling, authentication and
unavailable-service behavior. Preserve `/admin/` and `/api/public-status`
contracts. Do not create a dynamic port proxy. Maps, library, messaging and
diagnostics servers are not installed here; TLS unification is a separate review.
