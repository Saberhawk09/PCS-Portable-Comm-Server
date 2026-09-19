# Optional Starlink telemetry and staged lifecycle controls

Read-only telemetry and dashboard support were deployed on September 14, 2026.
The operator's Mini is not connected, so real Mini commissioning remains pending.
Starlink routing and PCS offline LAN operation remain independent.
The PCS-wide event logger is a separate task.

## Read-only diagnostics

The optional collector polls the Mini every 15 seconds and writes sanitized
`/run/pcs-starlink/status.json`. The homepage has Overview and Starlink navigation
at `/` and `/starlink/`; public information and admin views have a Starlink card.
`/api/v1/starlink` and the status resource expose only explicitly allowed metrics:
dish state, uptime, latency, loss, throughput, obstruction fraction, selected alert
names and sample age. The tab also displays the separately measured fourth INA226
branch power/energy when commissioned. It does not substitute dish power estimates
for that monitor.

No serial/device identifier, location, network address, raw RPC error, pairing
configuration or control setting is published. Missing fields remain unavailable;
stale samples lose their measurements instead of looking connected. Diagnostics
failure does not become a PCS hard fault or change route selection. This first
version supplies current diagnostics; historical charts and obstruction maps are
not included.

An absent Mini, disabled collector, failed reachability attempt or expired sample
has `available: false` and an informational `status: ok`; it does not raise a
Starlink-card warning, PCS alarm or fault. The state still reads disabled or
unavailable, never connected. A responding dish can report its own warnings,
which remain separate from overall PCS health. The uninstalled third and fourth
INA226 monitors remain disabled; the fourth is staged at `0x4e`.

Physical WAN presence is checked separately using the uplink manager's Ethernet
carrier and Internet probes. Connected Starlink Ethernet with failed Internet
gets a Starlink-card warning even if telemetry is disabled or unreachable. A
healthy fallback keeps PCS overall healthy. If no WAN provides Internet while
that Ethernet link is present, the Network card raises an overall no-uplink
warning instead of suppressing it as intentional offline operation. This is not
a hard LAN fault; the existing LCD/buzzer no-uplink warning remains applicable.

Local gRPC reflection is an unofficial firmware-dependent interface. The target
is fixed at `192.168.100.1:9200`; its TCP socket is explicitly bound to the selected
MAC-bound Ethernet uplink. A loopback-only relay supplies that bound socket to
gRPC. It cannot fall through Wi-Fi or cellular while Starlink is on standby.
The helper does not install routes, modify NetworkManager profiles, or open WAN
management ports. A three-second RPC deadline plus ten-second child-process
limit bounds reflection and requests. Responses and relay traffic are bounded.

## Installation and commissioning

Install or update the configured optional hardware using:

```sh
sudo ./scripts/setup-starlink-telemetry.sh --install
sudo ./scripts/setup-starlink-telemetry.sh --check
```

The installer needs Python venv/pip support and installs pinned optional packages
in `/opt/pcs-starlink`. Repeat installation preserves `/etc/pcs/starlink.json`.
Fresh configuration disables telemetry and every control. The matching normal
control-panel/frontend installation supplies cards, the tab and API routes;
installing the telemetry helper alone does not replace the frontend or dispatcher.

To commission, configure the existing Ethernet uplink ID, set `enabled: true`,
and restart only `pcs-starlink.service` during an authorized test window. The
uplink must be explicitly MAC-bound; a renamed USB NIC is resolved by its permanent
MAC and `eth0` is always excluded. Confirm the actual Mini exposes the expected
status schema, its measurements agree with its app, and telemetry remains reachable
when it is no longer the selected Internet uplink. The home-router USB NIC tests
do not establish this Mini-specific behavior.

## Pairing and API power actions

The root-only `pcs-starlink identify` command reads the connected Mini's device
identifier. After independently checking that it is the intended unit, record it
as `paired_device_id` and enable `allow_reboot`. `follow_pcs_reboot: true` separately
opts into coordinated PCS system reboot. Pairing here is an identity sanity check
on the selected physical uplink, not cryptographic authentication by the dish.
It does not change the Companion's certificate, pairing token or API permissions.

The existing authenticated API challenge and admin CSRF/confirmation protections
apply to `starlink-reboot` and the staged `starlink-shutdown`. Public pages contain
no control forms. `starlink-status` reads the cache. No endpoint accepts arbitrary
RPC messages or target addresses. A fresh device-ID check precedes the reboot RPC;
a mismatch refuses it. Unconfirmed/timeout reboot requests are never retried.

PCS `reboot-system` / `shutdown-system` actions use one transient systemd timer,
with a three-second delay so the request can return before connectivity changes.
The existing Pi-Star shutdown preparation remains intact. The Mini follow attempt
has an outer twelve-second limit; failure, absence or disabled pairing does not
block PCS reboot/poweroff. One pending PCS power action prevents duplicate queued
jobs. Individual service restarts do not reboot the Mini. Local admin and API use
the same dispatcher; the existing low-voltage shutdown path also remains bounded.

**Mini shutdown is staged, not implemented as a power-off RPC.** The inspected
local interface provides reboot/stow/sleep controls, but no verified true Mini
power-off. `starlink-shutdown` returns unsupported; `follow_pcs_shutdown` is a
reserved integration point that reports this limitation and lets PCS shut down.
No stow, sleep, arbitrary GPIO or untested shell hook is substituted. Actual power
following requires the planned DC switching design and hardware validation.
There is no boot-time Mini reboot or automatic restart loop.

Reference interfaces:
- [Community local status/control tooling](https://github.com/sparky8512/starlink-grpc-tools)
- [Starlink Mini setup guide](https://www.starlink.com/public-files/installation_guide_mini_kit.pdf)

## Local validation

Run Python unit tests, portal tests, shell syntax checks and the disposable-VM
fake-device integration test before commissioning. Never use real Mini reboot or
PCS power actions as a routine software test. Fixture tests must prove identity
mismatch rejection, timeouts without retry, public field filtering, stale-data
handling and PCS completion after a failed Mini follow request.

The transport fixture runs with the optional dependencies in a disposable Linux
VM, using an ephemeral loopback fake server and a real `SO_BINDTODEVICE` relay:

```sh
sudo /opt/pcs-starlink/bin/python tests/starlink_transport_integration.py --isolated-vm
```

It exercises real reflection, protobuf zero/uint64 decoding, public filtering,
identity mismatch rejection and exactly one fake reboot. It does not validate
physical Ethernet routing, Mini firmware compatibility or actual restart behavior.

Local validation on September 14, 2026: the full Python suite, 12 portal tests,
Python compilation and shell syntax checks passed in local testing. Real nginx
served `/starlink/` without control forms. The simulated dashboard was inspected
in a browser. Optional installation was repeated successfully in Debian 13 with
configuration preserved and dependency consistency checked. No live PCS or Mini
was contacted; power-action tests used mocks and the fake device.

Pre-deployment follow-up: 600 Python tests and 12 portal tests passed after
making optional absence informational, staging the fourth INA226 at `0x4e`,
and adding the fixed Starlink actions to both installer sudoers allowlists.
An enabled collector with no Mini was exercised under systemd in the disposable
VM: it remained active and reported `available: false`, `status: ok`. This test
does not enable or commission the uninstalled power monitors.

## Deployment validation: September 14, 2026

Code commit `f0182ba` was deployed over v1.9.1 with a rollback backup at
`/var/backups/pcs-starlink/1789393383538346955/rollback.py`. The final warning-policy
revision passed 602 Python tests; the portal suite passed 12 tests. Live checks
confirmed the homepage, Starlink tab, public/admin cards, HTTPS public allowlist,
protected action catalog and fixed dispatcher permissions. No power control was
executed. The collector is enabled, active and quietly unavailable without a Mini.

The third INA226 remains disabled at `0x4d`; the fourth remains disabled at `0x4e`.
Existing input/5V calibration and protection settings were preserved. The power
monitor, uplink controller, Dire Wolf, GPSD and Chrony kept their running processes;
TLS/pairing/admin credentials, NetworkManager profiles and eth0 LAN configuration
were unchanged. No new I2C sensor was probed or commissioned.

The first post-reboot self-test caught the existing IPv4-only WireGuard endpoint
check while the peer still used IPv6. Its existing refresh timer selected IPv4
without deployment changes; the repeat self-test passed all enabled checks with
PCS status OK. The final report is
`/root/.local/state/pcs/self-test-20260914T134758Z-8443.log`.
Real Mini telemetry, paired reboot and physical power monitoring still require
hardware commissioning. This deployment did not publish a GitHub release.


## v1.9.5 commissioned deployment

The operator confirmed the Mini Ethernet installation and Starlink DC branch.
Live inspection found healthy Starlink IPv4/IPv6 on eth1, fresh CONNECTED telemetry,
and the enabled 0x48 INA226 with 0.002-ohm / 20A configured calibration.
The normal base installer now updates an existing Starlink installation while
preserving private configuration and pairing. Generic installs remain optional.
The commissioned power profile includes the Starlink branch; aggregate battery
accounting and independent branch counters are described in `power-system.md`.
Mini true power-off still requires DC switching hardware; telemetry commissioning
does not make the unsupported shutdown hook capable of removing power.
