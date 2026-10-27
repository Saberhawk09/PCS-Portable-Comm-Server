# Authorized PCS UI deployment — October 26, 2026

The operator authorized PCS deployment and then explicitly approved draft PR
creation and GitHub CI. [PR #106](https://github.com/Saberhawk09/PCS-Portable-Comm-Server/pull/106)
remains a draft; no merge, version tag or GitHub Release was requested or created.

The exact implemented UI commit is
`f19ff958e569b3d5eb8e1f0a3dc8193f26b995b4`. Its [CI run](https://github.com/Saberhawk09/PCS-Portable-Comm-Server/actions/runs/40436206604)
passed all five jobs: Python/JavaScript/frontend, Windows witness, SNMP,
VLAN/recovery and bandwidth namespace integration.

PCS host identity was verified against the retained SSH host key. Its checkout
was clean at v2.2.2 (`ba80c8d`), the expected baseline. Preflight rendered the
candidate using real cached administrative telemetry, checked all 18 cards and
31 original action forms, and passed the ordinary-user appliance self-test.

## Activation and validation

Source switched to the exact candidate in detached HEAD. The existing strict
static stager created `/var/www/pcs-releases/site-88vh7pj6` and atomically
activated the site symlink. No nginx configuration, credential, privileged
helper, systemd unit, collector, power setting or network/radio policy changed.

The first attempt automatically rolled back because the service guard treated
the legacy redirect's coupled restart as unrelated. Inspection confirmed its
existing `Requires=pcs-control-panel.service` dependency. The guard was corrected
to permit this known web-service coupling, while preserving all other guards;
the same UI code then deployed successfully. Both attempts explicitly restarted
only the web backend; systemd also restarted the dependent port-8080 redirect.
nginx and all 15 audited unrelated service processes remained unchanged.

Final validation passed:

- All **58 protected configuration entries** retained their content, ownership,
  permissions and symlink targets, including network profiles, credentials,
  WireGuard, nginx and backup settings.
- Backend health, static assets/help pages, detailed status, public JSON,
  admin redirects/login cookie, legacy redirect and listener isolation passed.
- Unauthenticated admin/password/bandwidth requests still redirect to login;
  an unauthenticated action POST cannot dispatch. Public fields passed the
  existing allowlists.
- Real cached admin data rendered all **18 cards / 31 actions**. This was a
  server-side render check, not a production password login or execution of
  operator actions. Authenticated HTTP/CSRF workflows were covered by CI.
- PCS self-test passed before and after activation; zero failed systemd units.
- Live Chrome checks at **1440 and 360 px** passed every public section and
  the login page without document overflow or JavaScript errors. Real telemetry
  was fresh and overall status was **OK**, online over **Wi-Fi**. Starlink
  telemetry was unavailable before and after deployment; no repair or forced
  polling was attempted.

Live screenshots and raw deployment/browser receipts are retained locally in
`tmp/ui-deployment/` in the parent PCS workspace. They are deliberately separate
from the repository's labelled development-fixture screenshots.

## Rollback

The successful deployment's private backup is:

```sh
sudo /var/backups/pcs-ui-before-6r6npgfl/rollback.sh
```

It restores baseline source and the original static release
`/var/www/pcs-releases/site-y9cee6a3`, validates nginx and restarts the web backend
(plus its existing dependent redirect). The directory also retains source Git
bundle, configuration-integrity manifest, original process metadata, pre/post
self-test logs and the deployment receipt. The earlier automatically restored
attempt is separately retained at `/var/backups/pcs-ui-before-8tgxcl_a/`.

No reboot, shutdown, WAN failover, RF/PTT, DC-source change, password rotation or
privileged operator action was exercised. Those are not implied by UI acceptance.
