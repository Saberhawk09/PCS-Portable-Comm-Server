# Optional switch monitoring and boot-aware WAN policy

Development addendum to [the VLAN migration](managed-switch-vlan-migration.md).
No running appliance changes are part of this development. Hardware acceptance,
live deployment, cellular fault trials and RF work remain separate approval gates.
The parent VLAN migration must be commissioned independently before using VLAN
physical-link acceleration. Monitoring and both policy features default disabled.

## Behavior and boundaries

`pcs_switch_monitor.py` runs read-only Net-SNMP GET/WALK commands and writes
`/run/pcs-switch-monitor/status.json`. The existing uplink manager reads that
small cache without making SNMP requests. There are no dependencies from LAN,
routing or application units to this optional service. No SNMP SET is used.

The default poll interval is 5 seconds, per-request timeout 1 second, retries 0,
and staleness threshold 20 seconds. A complete cycle has a 5-second budget;
optional MIB objects may be omitted when that budget runs out. Responses are
bounded to 64 KiB and interface inventories to 128 entries. Credentials are
passed through a temporary private Net-SNMP configuration, never command-line
arguments, environment values, or subprocess error output. The service uses a
dynamic unprivileged user and a systemd credential. Its cache is readable by root.

Physical state is independent of internet health: `up`, `down`,
`administratively_down`, `unknown`, `stale`, or `monitor_unavailable`.
Unknown/unsupported/mismatched identity and poll failure cannot accelerate
failover. A decrease in switch uptime invalidates that observation; later fresh
polls must establish the state again. Service restart changes the observation
generation, discarding debounce credit but retaining diagnostic transition
history within the same PCS boot. The cache does not survive a reboot.

The optional startup policy uses the actual kernel boot ID and absolute uptime,
with a default deadline of 240 seconds after boot (configurable 0–900). Restarting
NetworkManager or the uplink manager cannot extend it. A saved early completion
also survives those restarts. A new kernel boot starts a new window; Linux boot ID
cannot distinguish electrical power-on from an OS reboot. Startup grace suppresses
only **automatic cellular activation** when the configured preferred Ethernet/VLAN
WAN has a lower priority number than a fallback cellular WAN. It does not suppress
probes, ordinary selection/recovery, LAN services, manual activation, or a connected
cellular session. Sustained healthy probes for the preferred WAN close the window.
Link-down observations never extend or cancel it. Missing SNMP cannot extend it.
Grace is independent of SNMP and can be enabled with monitoring disabled.

Acceleration is separately opt-in, applies only to the configured preferred VLAN
WAN and verified physical port 2, and needs at least two distinct fresh successful
polls over the debounce interval (default 10 seconds, configurable 5–60). It only
shortens that WAN's internet-failure hold. Successful probes still win; other
preferred WANs, cellular retry timing, manual suppression and ownership remain
in force. IPv4 and IPv6 retain independent health and recovery policies. A missing
Pi VLAN carrier disables port-only acceleration: trunk/switch power loss may also
remove the wired LAN and is reported separately. Software cannot restore an
unpowered switch. Existing DHCP renewal, routing, accounting, Dire Wolf recovery,
WireGuard management and buzzer policy are unchanged. Optional-monitor warnings
never become an overall alarm or a required self-test failure.

Traps are deferred. `traps_enabled: true` is rejected. There is no UDP 162 listener,
trap firewall rule or trap-driven policy path; missing, delayed or duplicate traps
have no effect. Polling alone reconciles link changes.

## D-Link and management security

The [DGS-1100-08V2 A1 manual](https://support.dlink.com/resource/PRODUCTS/DGS-1100-08V2/REVA/DGS-1100-08V2_REVA_MANUAL_v1.00_WW.pdf)
documents read-only communities, a maximum of 16 alphanumeric characters,
SNMPv1/v2c host settings, link and restart notifications, and a management VLAN.
Use the generated 16-character dedicated read-only community; never configure
read/write access for PCS. SNMPv1/v2c transport is unencrypted. SNMPv3, SSH, REST,
and exact MIB/index behavior are not assumed.

The [Net-SNMP client options](https://www.net-snmp.org/docs/man/snmpcmd.html) and
[private configuration mechanism](https://www.net-snmp.org/docs/man/snmp.conf.html)
are used for numeric OIDs/enums/ticks and credential isolation. Install the small
`snmp` client package only; production does not need `snmpd` or a monitoring stack.

The example address `10.42.0.4` must be reserved and verified conflict-free.
Keep the switch management plane off VLAN 20 and verify denial from a WAN-port
client, including IPv4, IPv6 and untagged traffic as applicable. If management is
on VLAN 10, peer LAN clients can reach it directly at Layer 2; Pi firewall rules
cannot prevent that. Apply only access controls actually supported by the switch.

VLAN 99 is a possible stronger isolation option, not implemented or automatically
introduced here. After verifying hardware behavior, a separately reviewed design
could use tagged VLAN 99 on the Pi trunk, a nonoverlapping private management
subnet with no gateway/default route, no client-port VLAN 99 membership, and
explicit Pi input/forwarding restrictions. An isolated temporary commissioning
port may be needed to avoid lockout. Verify that the switch management VLAN really
restricts all management services, that settings persist across power removal,
and that VLANs 10/20 cannot bypass it. The parent migration's 10/20 profiles do not
create VLAN 99; this option needs its own authorized topology change and rollback.

## Offline staging (no root or service operations)

Run in the reviewed repository checkout on a Linux development machine. Use a
new directory; `git archive` includes only committed sources, never local secrets.

```sh
stage=$(mktemp -d "${TMPDIR:-/tmp}/pcs-switch-stage.XXXXXX")
git archive HEAD | tar -x -C "$stage"
git rev-parse HEAD > "$stage/SOURCE_COMMIT"
python3 -m unittest discover -s "$stage/tests" -p test_switch_monitor.py -v
python3 -m compileall -q "$stage/scripts" "$stage/web"
printf 'Staged source: %s\n' "$stage"
```

This stages source only. No VLAN profile, NetworkManager connection, firewall,
service installation or restart occurs. The real Net-SNMP integration test is
`sudo python3 tests/integration_switch_snmp.py` on a disposable Linux development
host with `snmp`, `snmpd` and `iproute2`; it verifies a new network namespace before
configuring its synthetic loopback address. Never use hardware for general CI.

## Future first installation — only after separate deployment approval

These commands assume the reviewed parent PCS version is installed, the existing
uplink manager and statistics API are active, and this optional monitor has never
been installed. Run from the staged source directory. Keep a local console and
the parent migration recovery plan. Stop if these prerequisites differ; do not
use this first-install recipe to overwrite another commissioned monitor.

```sh
sudo test -f /usr/local/lib/pcs/pcs_uplink_manager.py
sudo test -f /usr/local/sbin/pcs-web-action
sudo test -f /usr/local/lib/pcs-stats-api/pcs_stats_api.py
sudo systemctl is-active --quiet pcs-uplink-manager.service
sudo systemctl is-active --quiet pcs-stats-api.service
sudo test ! -e /etc/pcs/switch-monitor.json
sudo test ! -e /etc/pcs/switch-monitor.community
sudo test ! -e /usr/local/lib/pcs/pcs_switch_monitor.py
sudo test ! -e /etc/systemd/system/pcs-switch-monitor.service
```

After all checks pass, back up the exact replaced files and policy, recording the
printed backup directory for rollback. The credentials are not part of this archive.

```sh
backup=$(sudo mktemp -d /var/backups/pcs-switch-before.XXXXXX)
sudo tar -C / -cpf "$backup/files.tar"   usr/local/lib/pcs/pcs_uplink_manager.py   usr/local/sbin/pcs-web-action   usr/local/lib/pcs-stats-api/pcs_stats_api.py etc/pcs/uplinks.json
printf 'Rollback directory: %s\n' "$backup"
sudo apt-get install snmp
sudo install -m 0755 scripts/pcs_switch_monitor.py /usr/local/lib/pcs/pcs_switch_monitor.py
sudo install -m 0755 scripts/pcs_uplink_manager.py /usr/local/lib/pcs/pcs_uplink_manager.py
sudo install -m 0755 scripts/pcs-web-action.sh /usr/local/sbin/pcs-web-action
sudo install -m 0755 web/pcs-control-panel/pcs_stats_api.py /usr/local/lib/pcs-stats-api/pcs_stats_api.py
sudo install -m 0644 config/switch-monitor.example.json /etc/pcs/switch-monitor.json
sudo install -m 0644 systemd/pcs-switch-monitor.service /etc/systemd/system/pcs-switch-monitor.service
sudo python3 /usr/local/lib/pcs/pcs_switch_monitor.py --generate-community   --output /etc/pcs/switch-monitor.community
sudo systemctl daemon-reload
```

The nonsecret JSON must remain root-owned and readable by the dynamic service
user (0644). Keep the credential root-owned 0600. Transfer its value through a
private local administration session to the switch's **read-only** community
setting; do not paste it into command arguments, logs, Git, screenshots or tickets.
These installation commands do not enable or start monitoring or restart routing.

## Commissioning discovery and cable verification

Record hardware revision and firmware version, check official applicable firmware
updates, and record the authorized VLAN/management configuration. Verify a full
power removal retains it. Confirm management-plane isolation before polling.
Inspect/edit `/etc/pcs/switch-monitor.json` for the verified management address and
SNMP version, keeping `enabled: false` and `mapping: null` during discovery.
Discovery is an explicit read-only network operation even while the daemon is disabled.

```sh
sudo install -d -m 0700 /var/lib/pcs-switch-commissioning
sudo python3 /usr/local/lib/pcs/pcs_switch_monitor.py --discover   --output /var/lib/pcs-switch-commissioning/before.json
# Physically unplug ONLY the known Starlink cable from physical switch port 2.
sudo python3 /usr/local/lib/pcs/pcs_switch_monitor.py --discover   --output /var/lib/pcs-switch-commissioning/disconnected.json
# Reconnect that same cable to physical port 2 and wait for link negotiation.
sudo python3 /usr/local/lib/pcs/pcs_switch_monitor.py --discover   --output /var/lib/pcs-switch-commissioning/reconnected.json
```

Inspect these private snapshots. Identify the single interface index demonstrating
administratively enabled up/down/up while every other interface stays unchanged.
Do not assume index 2. Substitute the actually observed index below (17 is only
an example), completing all snapshots within ten minutes without a switch reboot.

```sh
sudo python3 /usr/local/lib/pcs/pcs_switch_monitor.py --verify-mapping --physical-port 2 --ifindex 17   --before /var/lib/pcs-switch-commissioning/before.json   --disconnected /var/lib/pcs-switch-commissioning/disconnected.json   --reconnected /var/lib/pcs-switch-commissioning/reconnected.json   --output /var/lib/pcs-switch-commissioning/verified.json
sudo install -m 0644 /var/lib/pcs-switch-commissioning/verified.json /etc/pcs/switch-monitor.json
```

Verification saves a disabled configuration. The fingerprint covers sysDescr,
sysObjectID and the index/description inventory. It is not a hardware serial number
or cryptographic device authentication. If firmware/configuration changes without
changing those objects, software cannot discover that hidden change. Always disable
monitoring and repeat cable verification after firmware, switch replacement or
port-configuration changes. Ambiguous or unsupported mapping remains unknown.

## Explicit activation and policy controls

First authorize observation alone. With `sudoedit /etc/pcs/switch-monitor.json`,
change only `enabled` to `true`, keeping the verified mapping. Then:

```sh
sudo systemctl enable --now pcs-switch-monitor.service
sudo python3 /usr/local/lib/pcs/pcs_switch_monitor.py --check
sudo systemctl restart pcs-uplink-manager.service pcs-stats-api.service
sudo /usr/local/sbin/pcs-uplink-manager --check
sudo /usr/local/sbin/pcs-status
sudo /usr/local/sbin/pcs-self-test
```

The explicit restart loads the reviewed policy implementation and API renderer.
Authenticated network diagnostics show physical state, cache age, last poll and
transition, optional speed, trunk carrier, grace remaining and acceleration.
Existing rows provide DHCP/address state, internet health and selected uplink.
Public dashboard output omits the new private diagnostics; the authenticated API
uses a nested allowlist that also excludes management address, index, fingerprint,
credential and raw error text. No extra Starlink polling is introduced.

For the policy trial, edit `/etc/pcs/uplinks.json` with `sudoedit`, preserving every
existing uplink/profile, priority and setting, and add these top-level fields:

```json
"startup_grace_enabled": true,
"startup_grace_seconds": 240,
"startup_grace_uplink": "starlink",
"switch_assist": false,
"physical_debounce_seconds": 10
```

Validate before a deliberate restart:

```sh
sudo python3 -c 'import sys; sys.path.insert(0,"/usr/local/lib/pcs"); from pcs_uplink_manager import load_config; load_config(); print("Valid uplink policy")'
sudo systemctl restart pcs-uplink-manager.service
```

A restart after boot second 240 cannot create a fresh grace trial. Schedule a
separately authorized complete PCS/Starlink power cycle to test startup. After
observation and grace acceptance, explicitly change `switch_assist` to `true`,
validate and restart again for supervised failover trials. The repository uplink
installer also accepts `--startup-grace-seconds`, `--startup-grace-uplink`,
`--switch-assist yes|no`, and `--physical-debounce-seconds`; it is a full installer
with existing service/profile actions, **not** an offline staging command.

## Disable and rollback

To return to standard health timers, set `switch_assist: false` and
`startup_grace_enabled: false` in `/etc/pcs/uplinks.json`, validate as above, then:

```sh
sudo systemctl restart pcs-uplink-manager.service
sudo systemctl disable --now pcs-switch-monitor.service
```

Set monitoring `enabled: false` in `/etc/pcs/switch-monitor.json` as well. A stopped
monitor otherwise becomes stale automatically; it cannot indefinitely hold failover.
The existing manager does not require the optional module or service.

For full rollback of the first-install recipe, use the recorded backup directory
in place of the example below. Verify `files.tar` contains exactly the four backed
up paths before restoring it. Preserve any later authorized edits separately first.

```sh
backup=/var/backups/pcs-switch-before.REPLACE_WITH_RECORDED_SUFFIX
sudo tar -tf "$backup/files.tar"
sudo systemctl disable --now pcs-switch-monitor.service
sudo systemctl stop pcs-uplink-manager.service pcs-stats-api.service
sudo tar -C / -xpf "$backup/files.tar"
sudo rm -f /usr/local/lib/pcs/pcs_switch_monitor.py /etc/systemd/system/pcs-switch-monitor.service
sudo systemctl daemon-reload
sudo systemctl start pcs-uplink-manager.service pcs-stats-api.service
sudo /usr/local/sbin/pcs-uplink-manager --check
sudo /usr/local/sbin/pcs-self-test
```

Keep the now-unused protected community and disabled JSON as commissioning records,
or separately revoke the switch community and securely remove them. This rollback
does not alter NetworkManager profiles, VLANs, firewall, switch settings, or cellular
sessions, and is distinct from the parent VLAN migration rollback.

## Acceptance evidence still required

All automated checks use mocks or a synthetic agent. They do not prove D-Link MIB
support, physical mapping, firmware persistence, electrical cold boot, Starlink
service recovery, live carrier behavior, RF coexistence or paid-cellular acceptance.

Record timestamps and authenticated status for each supervised trial:

1. Confirm exact revision/firmware, private RO credentials, isolation and persistent VLAN settings.
2. Verify discovery/cable mapping, optional object support, switch restart, mapping mismatch and SNMP loss. Neither a timeout nor a switch reboot may create a confirmed port-down event.
3. Power-cycle PCS and Starlink together: LAN/apps start immediately, probes continue, no automatic cellular before the boot deadline; sustained Starlink health closes grace early.
4. Restart only NetworkManager and the manager during grace. Remaining time must fall, never restart. Keep an operator-connected cellular session; it must remain unowned and usable. Verify manual connect during grace.
5. After startup, disconnect the known cable and then test a logical internet outage with carrier up. Check debounce plus failed probes, ordinary timers when SNMP is disabled, IPv4/IPv6, priorities, accounting and ownership-preserving recovery. Repeated link flaps must not recreate grace.
6. Remove switch power under supervision: report trunk loss, preserve available Wi-Fi/cellular policy, and do not claim a Starlink-only outage or restored wired LAN.
7. Run PCS self-test and existing network qualification. Verify APRS/Dire Wolf, Meshtastic, GPSD/NTP, Samba, WireGuard, authenticated/public dashboards, alert behavior and unattended startup. RF and WAN-fault trials require their own explicit authorization.

Traps need no acceptance in this polling-only release. A future receiver must validate
sender, event type and verified index and may only request a fresh poll, never
change routing directly.
