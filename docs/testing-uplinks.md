# Multi-WAN acceptance tests

No live PCS deployment is authorized by this document. Use an isolated Debian
13/Python 3.13 NetworkManager VM first, then an approved appliance test window.

## Automated local checks

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_pcs_home.js
python3 -m compileall -q scripts web tests
for script in scripts/*.sh; do bash -n "$script"; done
```

`test_uplink_manager.py` covers priority, DHCP/carrier without Internet, failure
and recovery windows, transient loss/recovery, optional Starlink, manual and
owned activation restarts, replaced-session ownership, suppression, bounded
probes, accounting continuity, protected identity and public cache filtering.

## Isolated NetworkManager integration

```sh
sudo python3 tests/integration_uplinks.py --isolated-vm
```

The harness refuses an existing `eth0`, creates only its test LAN and `pcsut*`
interfaces/namespaces, and removes those resources on exit. It translates the
virtual Ethernet device type only in its test adapter; production discovery
still requires real Ethernet, Wi-Fi or modem types.

Use veth interfaces with distinct DHCP upstreams and a simulated LAN client.
Run the root-only integration harness only inside a disposable VM. Validate
that D-Bus Reapply changes DHCP default metrics without disconnecting, that
strict reverse-path filtering cannot invalidate standby checks, and that
DHCP renewal, overlapping upstream subnets, missing routes, IPv6 and VPN policy
do not silently bypass selection. Test repeat setup and migration from both
legacy modes with a manually active connection. Missing optional Ethernet
hardware must not prevent installation or LAN operation.

## Spare USB NIC and Starlink gates

For the EM7565 regression, manually connect cellular while Ethernet is healthy,
then disable Wi-Fi and disconnect Ethernet. Verify the cellular address, DNS,
default route and exact active-session identity survive standby and failover.
Restore Ethernet and wait for recovery: cellular must remain connected and
unowned. Repeat after restarting the uplink controller. There must be no modem
`device-reapply` audit entry. A connected-modem test must verify kernel IP/routes
and bound Internet probes, not only NetworkManager's activated state.

### EM7565 recovery verification — 2026-09-14

With Wi-Fi disabled and Ethernet unplugged, the modem remained activated but
lost its kernel address and DNS after the old controller's standby Reapply.
One operator-approved reconnect with the controller paused restored its IPv4
address, metric-900 default route and successful bound Internet probes. The
patched controller then started and restarted without changing the manual
activation identity or taking ownership. Cellular remained healthy and selected,
the public dashboard reported online, and no systemd units were failed. A Windows
LAN client bound to its PCS address completed an HTTPS request with HTTP 200.
All 26 uplink unit tests passed on PCS Linux, including the new no-modem-Reapply
regressions. Actual Ethernet reconnection/failback with this patch remains a
physical acceptance check; state-machine recovery and restart were tested.

For every transition, record `ip -j address`, `ip -j route`, NetworkManager active
profiles, cached status, public/admin API output, and an actual LAN-client fetch.
Confirm `eth0` remains `10.42.0.1/24`; renew a client's DHCP lease and verify
gateway/DNS/NTP `10.42.0.1`. Test local names, Samba, homepage, GPSD, NTP, local
APRS/Meshtastic and Pi-Star while every WAN is unavailable.

1. Spare USB NIC with any DHCP upstream: discover `enx...`, explicitly bind its
   MAC, acquire DHCP, pass bound ping, and forward LAN-client Internet traffic.
2. Starlink normal-router mode: supplied power adapter is sufficient; prove
   DHCP and client Internet through double NAT.
3. Remove Ethernet: fail over after the failure window to Wi-Fi or cellular.
4. Keep Ethernet carrier and DHCP but remove upstream Internet: verify effective
   client traffic moves to Wi-Fi. If Wi-Fi also fails, cellular activates after
   stability and is marked owned. A manually active session remains unowned.
5. Restore Starlink: a single success must not fail back; sustained recovery
   must restore preference and release only the owned cellular activation.
6. Reboot with Starlink present, then absent. Validate unattended startup and
   Wi-Fi/cellular/offline operation with intact local services.
7. Restart the service with manually active and manager-owned cellular sessions;
   verify exact identity handling. Disconnect/reconnect manually between polls.
8. Transfer known-size files through each WAN, compare kernel counter deltas,
   restart the observer, unplug/replug the NIC, and reboot. Check aggregate and
   per-WAN totals, partial-data labels and public identity redaction.
9. Run `pcs-status.sh`, `pcs-self-test.sh`, and setup `--check` after transitions.

Physical tests and Starlink-specific results remain pending until recorded.

## Local validation record — 2026-09-13

The disposable Debian 13 VM demonstrated real NetworkManager DHCP/Reapply,
interface-bound IPv4/IPv6 probes, strict-RPF handling, and a forwarded LAN
client moving between two simulated WAN routers during a carrier-up upstream
outage and recovery. IPv6 was failed/recovered independently. Saved profile
metrics and the shared LAN profile were preserved. These tests use simulated
upstreams and do not establish actual USB/Starlink or modem performance.

Fresh/repeat setup, absent optional Starlink, auto/manual policy changes, and
rollback following an injected post-install failure were tested in that VM.
Migration from a running legacy automatic controller preserved custom Wi-Fi,
cellular profile and timing settings and left only the new controller enabled.
Public/private nested-field filtering, accounting and state-machine behavior
are covered by the automated Python tests. The public portal was inspected at
desktop width and at 390 pixels using synthetic counters, not live telemetry.

The final Debian run passed 555 Python tests, compilation and shell syntax
checks; all 10 portal JavaScript tests passed. Explicit manual policy during
legacy migration is covered by a regression test.

## Authorized appliance deployment — 2026-09-13

The manager and dashboard changes were deployed selectively over the live
v1.9 source baseline, preserving its recovery installer and unrelated power
configuration. The existing automatic Wi-Fi/cellular policy migrated; the old
controller is disabled. No USB Ethernet NIC was present, so Starlink remains
unconfigured and optional.

Live IPv4/IPv6 Wi-Fi health passed, cellular stayed inactive and unowned,
and `eth0` retained `10.42.0.1/24` and its saved NetworkManager profile. Public
and private dashboard counters, nested public redaction, static portal output,
and certificate-validated HTTPS API smoke checks passed. An observer-only
restart preserved counters and session protection. Power-monitor and Dire Wolf
process IDs and restart counts were unchanged. No systemd units were failed.

`pcs-status.sh` reported Wi-Fi healthy/active. `pcs-self-test.sh` reported only
the expected uncommitted-source warning. Usage is marked partial because the
observer was installed mid-boot. Authenticated HTTPS token checks were not run;
private dashboard data was verified through the privileged local collector.

The timed deployment rollback was disarmed after independent verification;
its backup remains at
`/var/backups/pcs-uplink-deployment/1789333720723355779`. The generated
`rollback.py` restores the previous runtime and source installation. Physical
USB/Starlink, actual modem failover, LAN-client transition and reboot tests
remain pending; the simulated tests above do not replace those gates.

### USB NIC commissioning, same day

The ASIX AX88179 adapter was detected on a 5000M USB bus and configured as
`pcs-starlink-uplink`, bound to its permanent MAC with no interface-name
restriction. Its current Linux name is `eth1`; the binding does not depend on
that name or the selected USB port. Non-root discovery initially missed
`ethtool` outside the operator's PATH; the installer now resolves its Debian
path, with a passing regression test (24 uplink tests).

Connected to the operator's home router, the adapter acquired DHCP and passed
bound Internet probes. After recovery hysteresis it became the effective IPv4
and IPv6 WAN, with Wi-Fi healthy in standby and cellular inactive/unowned.
Public status confirmed the transition and included per-WAN counters. The PCS
LAN address and power-monitor/Dire Wolf processes remained unchanged. The
dashboard's Starlink label currently identifies this test Ethernet profile;
these results are for the home upstream, not the satellite service. USB-port
relocation and physical outages remain pending.

### Indicator correction and LAN-client verification

The older GPIO uplink classifier incorrectly treated Ethernet as Offline,
despite healthy routing and dashboard status. Its existing indicators now use
the manager's cached health; Ethernet produces a healthy network LED, while
stale data is Unknown. No new LCD layout was added. All 90 GPIO tests passed,
including the Ethernet/failed/stale-cache regression test. Only the matrix and
status-LED services restarted; power monitoring and Dire Wolf remained running.
Live indicator logs then reported Ethernet, a green network LED and no alerts.

After Windows obtained PCS DHCP address `10.42.0.232`, direct LAN SSH worked.
An HTTPS request explicitly bound to that client address returned HTTP 200
while the Pi's effective WAN was USB Ethernet, validating PCS LAN-client
Internet access through the new WAN.

### LCD, reboot recovery and trusted Ethernet access — 2026-09-14

The LCD service now explicitly displays `Ethernet WAN`; its cached-health and
label tests pass. The APRS recovery helper separates engine stop, PTT-guard
settlement and engine start. A disposable systemd test reproduced the original
restart cancellation and verified the revised sequence. Following the operator's
reboot, the engine and local services were healthy with no failed units.

The USB NIC was moved directly to a Pi USB 3 port, with the flash-drive extension
on USB 2. Four fresh GNSS samples over 30 seconds reported a valid fix with 8–9
satellites used and no alerts. This is an observation, not proof of the earlier
interference mechanism or long-duration RF immunity.

The trusted home subnet was explicitly enabled for the MAC-bound Ethernet WAN.
Windows fetched the homepage through the Ethernet address (HTTP 200), and the
HTTPS network API returned 200 with certificate verification using the existing
certificate-covered `pcs.local` name resolved to that address for the request.
The new DHCP IP itself is not covered by the existing certificate; DNS or a
separately authorized certificate migration is required for normal hostname use.
Both existing firewall services were reloaded and checked; the grants and both
requests continued working. API identity and protected service PIDs were preserved.

The isolated nftables test verifies default denial, trusted web/API access,
untrusted-source rejection, repeated refresh, interface rename and withdrawal:

```sh
sudo ./tests/integration_uplink_management.sh --isolated-vm
sudo ./tests/integration_uplink_recovery.sh --isolated-vm
```

These harnesses create only their own test resources and must run in a disposable
systemd VM, never on the appliance. Starlink satellite operation, prolonged RF
coexistence, and physical modem/outage acceptance remain separate field gates.

The clean v1.9.1 release tree passed all 574 Python tests on Debian 13, all
10 portal JavaScript tests, Python compilation and shell syntax checks. Both
tracked nftables and systemd integration harnesses passed against that tree.
