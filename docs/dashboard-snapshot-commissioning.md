# Dashboard snapshot deployment and commissioning progress

Validated on PCS on 2026-10-10, starting from `2296538` (v2.2.0).

## Dashboard architecture

`pcs-dashboard-snapshot.service` is the sole background writer. A Linux flock
prevents concurrent writers, and timed-out collectors have their entire process
group terminated. It uses the existing distinct public/admin collector projections,
writing each through an atomic replace into root-only `/run/pcs-dashboard`.
The directory is boot-local. Snapshot reads never launch a collector.

The fixed privileged dispatcher returns snapshots directly, without launching a
transient systemd unit. Initial absence returns a loading response; snapshots older
than 45 seconds are explicitly stale and retain their original collection timestamp.
The public page refreshes every ten seconds. HTTP process caches last one second.
The Stats API retains its existing schema and allowlists, adding optional
`health.freshness` (`fresh`, `stale`, `unavailable`) to the documented v1 contract.

Repeated subprocess calls are memoized within one collection, systemd state is
batched, independent probes overlap, and Chrony queries disable hostname resolution.
Internet availability follows fresh uplink-manager health; the fallback IP probe is
used only when that cache is unavailable. DNS uses bounded name resolution, not a
ping to a hostname. Failure of an unrelated hostname ping must not override a
healthy selected uplink or falsely label PCS offline.

Self-test detection uses configuration existence, not unprivileged readability.
`/etc/pcs/uplinks.json` remains root:root 0600. Configuration checking uses sudo,
manager service activity/enabling is checked, and enabled/running legacy fallback
is rejected. Stats API privacy validation retains its strict redaction check with
a two-second response deadline.

## Measurements and acceptance

- Before: homepage 14.079 seconds; subsequent cached status 0.0018 seconds.
- After: homepage initial measured response 0.189 seconds; warm about 0.005 seconds.
- Stats API: initial measured response 0.203 seconds; warm about 0.026 seconds.
- No-snapshot loading response: 0.217 seconds, with useful loading text.
- Latest public background collection: 2.431-2.793 seconds.
- Latest admin background collection: 8.235 seconds; optional enrichment remains
  above the three-second target but never blocks HTTP requests.
- 104 focused Linux regressions passed before deployment. Subsequent Linux singleton
  writer integration passed, and the false-offline regression passed locally with
  live dashboard/API confirmation: Wi-Fi selected, internet and DNS available,
  offline false. A final regression run records the combined updated test count.
- Full PCS self-test: no failures; one warning for the deliberately uncommitted
  deployed patch. Both generalized-manager checks and legacy-controller checks pass.
- Public Stats API privacy checked live. SNMP/startup diagnostics remain private.

## Deployment and rollback

The operator explicitly approved deployment and restart of dashboard/API only.
The installed snapshot service is enabled. Networking services, VLANs, radio,
WireGuard, cellular profiles and WAN policy were not changed.

The exact replaced live files were archived in:
`/var/backups/pcs-dashboard-before.12lS2z/files.tar`.

Rollback on PCS:

```sh
sudo /var/backups/pcs-dashboard-before.12lS2z/rollback.sh
```

This disables the new collector, restores the replaced dispatcher, self-test,
control-panel and Stats API files, removes the new snapshot service/module, and
restarts only the dashboard and API. It does not restore or alter WAN policy.
An earlier backup-only attempt stopped before any replacement because the self-test
runs from the repository, not `/usr/local/sbin`; that incomplete archive is not the
rollback archive above.

The local branch is `codex/dashboard-snapshot-startup`. Live runtime deployment
is distinct from GitHub publication; no release or tag was published.

## SNMP stage

The existing monitor implementation passed 19 synthetic tests; the existing uplink
manager passed 34. The SNMP client package was installed without installing snmpd.
A dedicated 16-character alphanumeric community was created directly in
`/etc/pcs/switch-monitor.community`, root:root 0600, and was never printed.
The existing monitor configuration remains disabled, SNMPv2c, five-second polling,
traps disabled, and mapping unset. The switch has not been reconfigured by this task.

Operator switch setup, management-plane isolation verification, and supervised
physical-port-2 connected/disconnected/reconnected observations remain required.
Do not enable monitoring until mapping verification succeeds. Physical assistance
and revised startup behavior remain unchanged pending commissioning and automated
policy validation. No reboot, WAN cable test, or cold-boot acceptance occurred.


## Completed SNMP and startup enablement (2026-10-11)

The operator performed the physical-port-2 connected/disconnected/reconnected
cycle using a local router, not the Starlink Mini. Only interface index 2 changed
up/down/up; switch identity and other interface states remained unchanged. The
60.7-second cycle verified physical port 2 -> ifIndex 2. Evidence is protected at
`/var/lib/pcs-switch-commissioning/20261011T013519Z/` on PCS.

Direct read-only probes addressed to 10.42.0.4 received SNMP and HTTP responses
on VLAN 10 and no responses on VLAN 20 (two trials per service and VLAN).
No interface, route, VLAN membership or firewall change was made for the probes.
This evidence covers the configured IPv4 management address and these services.

Five-second monitoring is now enabled. Three consecutive polls confirmed link up,
1 Gbps speed, freshness, and no polling errors. Authenticated dashboard diagnostics
and the restricted root uplink service both consume the monitor cache. Public API
privacy checks still pass. The original community remains root:root 0600.

Two commissioning fixes were necessary:

- Accept root-owned 0440 only inside systemd's protected read-only credential mount;
  ordinary credential files retain their strict private-permission requirement.
- Install `acl` and give root an explicit read/traverse ACL on the dynamic-user
  runtime directory, inherited by atomic status files. This permits the restricted
  uplink service to read diagnostics without broad DAC capabilities or public files.

After 185 combined Linux tests passed, the revised manager was installed and
`switch_assist` changed from false to true. All other policy configuration remains
unchanged, including startup grace 240 seconds, failure/recovery 30 seconds, and
physical debounce 10 seconds. This boot's grace was already expired when enabled.

A confirmed, debounced physical disconnect can temporarily release startup grace
only when all preferred IPv4/IPv6 observations are known failures. Reconnection or
unknown physical state restores any remaining absolute grace. Cellular activation
waits for one idle, profile-compatible, registered modem. Readiness waiting does not
consume retry backoff; healthy preferred IPv6 also prevents paid activation.
Manual session ownership, suppression, priorities and recovery hysteresis remain.

Live acceptance after enablement: Ethernet WAN selected and internet healthy,
SNMP link up at 1 Gbps, physical acceleration inactive while link is up, cellular
inactive/unowned, HTTP 200 responses about 0.24 seconds, and public privacy valid.
The final route self-test was additionally corrected to use the fresh root-written
cache rather than reopen root-only configuration; all five focused Linux self-test
regressions passed.

Startup rollback:

```sh
sudo /var/backups/pcs-startup-before.u7ibyzoq/rollback.sh
```

The follow-up route self-test's previous copy is also preserved at
`/var/backups/pcs-startup-before.u7ibyzoq/pcs-self-test.before.sh`.

SNMP rollback, after disabling startup assistance via the startup rollback above:
stop/disable `pcs-switch-monitor.service`, restore `switch-monitor.before.json`
from the protected commissioning directory to `/etc/pcs/switch-monitor.json`,
restore `pcs_switch_monitor.before.py` and `pcs-switch-monitor.before.service`
to their original installed paths, and daemon-reload. Keep the private credential.
The existing `snmp` and `acl` client packages can remain installed.

Remaining operator acceptance: no-WAN/no-Wi-Fi cold boot, actual Starlink-connected
cold boot, cellular internet validation and sustained preferred-WAN recovery.
The user has been asked to authorize and prepare these disruptive tests; no reboot
or additional WAN interruption has been performed. Deployment is not a claim of
cold-boot or Starlink hardware acceptance. No GitHub publication occurred.


Final full self-test after the route-detection fix: no failures; only the expected
uncommitted-checkout warning. Detailed live evidence:
`/home/pi/.local/state/pcs/self-test-20261011T133423Z-1118118.log`.
