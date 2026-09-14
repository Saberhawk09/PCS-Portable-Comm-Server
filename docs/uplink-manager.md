# PCS uplink manager

The Pi remains the gateway, DHCP/DNS/NAT and local NTP server. `eth0` remains
`10.42.0.1/24`; clients use `10.42.0.1` and the EA4500 remains a bridge/AP at
`10.42.0.2`. No WAN is bridged into the LAN. Internet loss is intentionally
offline operation, not a LAN fault.

## Installation and migration

Stage and validate software before authorizing installation on PCS. The setup
commands below change networking when run without `--list` or `--check`; do not
run them on a remote appliance without a tested recovery path.

```sh
sudo ./scripts/install-dependencies.sh
./scripts/setup-uplink-manager.sh --list
sudo ./scripts/setup-uplink-manager.sh --interface enx001122334455 --mode auto
./scripts/setup-uplink-manager.sh --check
```

The operator explicitly selects the USB NIC. A present NIC supplies its MAC;
use `--mac 00:11:22:33:44:55` to stage an absent device. No candidate is silently
selected. The generated `pcs-starlink-uplink` profile binds to the NIC MAC and
uses DHCP. No Starlink connection or power hardware is required for setup.
Starlink router mode and initial double NAT are supported. Bypass is optional
and still uses DHCP. There is no dependency on unofficial Starlink APIs.

Without a Starlink selection, setup retains Wi-Fi and the available cellular
profile. Fresh installation defaults to manual policy. Legacy `wifi-fallback`
and `auto` map to automatic mode; legacy profile and timing settings migrate.
Explicit new settings override legacy settings; an existing JSON configuration
is preserved on repeat installation except for explicitly requested changes.
`setup-cellular-profile.sh --fallback manual|wifi-fallback` remains supported.

The old controller is stopped/disabled before the replacement starts. The units
conflict, and the old helper refuses its old policy when the new configuration
exists. An ambiguous old profile-only ownership marker is not adopted: an
existing cellular session stays connected and unowned.

## Configuration and routing

`/etc/pcs/uplinks.json` is authoritative. See
[`config/uplinks.example.json`](../config/uplinks.example.json). Entries have
`id`, `name`, `type`, `priority`, interface/MAC identity, optional profile UUID,
and `activation` (`observe` or `fallback`). Lower priority numbers win. Wi-Fi
observes any active saved profile on its configured interface. Cellular uses
its explicit UUID and `fallback`; its profile always has autoconnect disabled.
For another Ethernet WAN, create a MAC-bound NetworkManager DHCP profile and
add an entry with a distinct identity, profile UUID and priority. Do not use
the LAN NIC or a WAN subnet overlapping `10.42.0.0/24`.

A bound Ethernet NIC also observes an operator-selected active WAN profile;
the configured UUID is used for activation, not to claim an existing session.

The observer runs in both modes so usage totals and health remain available.
Automatic mode uses 10-second polling, 30-second sustained failure and recovery
windows, and interface-bound IP-literal probes. Either target may succeed.
Each ping has a two-second default reply timeout and an outer process deadline.
Activation retries are limited to once per minute. Unknown collection/probe
errors do not authorize starting expensive cellular fallback.

The controller changes **applied** NetworkManager settings through versioned
D-Bus `GetAppliedConnection`/`Reapply`; it does not rewrite saved Wi-Fi profiles,
delete raw routes, or bounce interfaces. Selected IPv4 routes use metric 50,
healthy standby routes 1000 + configured priority, and unhealthy routes
20000 + priority. A selected path retains preference during failure hysteresis.
IPv6 is probed and selected independently with the same stability windows;
this does not enable IPv6 on the PCS LAN. A connected failed path retains its
gateway and routes so bound probes can detect recovery. When all paths fail,
the status reports offline even if a demoted default remains installed.
Cellular is an exception to runtime Reapply: NetworkManager 1.52.1 on the
EM7565 can discard its bearer-provided address and DNS during a metric update,
while still reporting the connection as activated. The manager leaves modem
routes and DNS untouched. It reads installed cellular default metrics and puts
the selected Ethernet/Wi-Fi route below them and standby/unhealthy routes above
them. Cellular therefore takes over without reconnecting its bearer. The usual
cellular metric remains 900; custom metrics are handled relative to the installed
route. A metric without room for a preferred route is reported as a routing error.
Multiple simultaneously connected cellular modems retain their fixed route
ordering; automatic selection between them requires a separate routing design.
Additional Ethernet WANs remain supported.

An existing cellular session whose address was already lost must be reconnected
by the operator (or with explicit permission); restarting the manager alone does
not restore it. Old journal entries never authorize a modem Reapply or reconnect.

Physical link removal can make NetworkManager withdraw its route immediately;
already-connected standby traffic can then move earlier than the controller's
failure window. Paid cellular activation still waits for sustained failure.

On configured WANs only, strict IPv4 reverse-path filtering is temporarily
changed to loose filtering so standby probe replies are accepted. The global
and LAN settings are untouched. Runtime originals are journaled and restored
when switching to manual mode. DNS priorities follow health ordering while
preserving negative priorities used by split-DNS configurations. Existing VPN
routes are never changed; a VPN/policy route that prevents verification of the
selected physical WAN is reported as a routing error. DNS reachability remains
a separate dashboard signal. Existing TCP connections may need to reconnect
after their public source address changes; this is failover, not seamless
session migration or load balancing.

## Ownership and operator control

The existing buzzer service emits one short beep when automatic WAN selection
changes, including confirmed offline/recovery transitions. Initial observation,
buzzer restart, stale status and standby-only changes are silent. The selection
already includes uplink hysteresis, so individual failed probes do not beep.
Mute, startup chimes and higher-priority alarms suppress the notification;
suppressed notifications are not replayed later. Manual mode observes the
effective Internet source. No additional process owns the buzzer GPIO.

Automation records boot, NetworkManager daemon and active-connection identity,
not merely a profile name. Only the exact activation it created can be released.
Restarting the manager preserves that identity. A replaced activation, reboot,
or NetworkManager restart invalidates ownership. Manual connections participate
in priority routing but are never automatically disconnected.

Dashboard Connect Cellular relinquishes automatic ownership. Disconnect
Cellular also suppresses automatic cellular activation for the current boot.
Connect Cellular or the following command re-enables eligibility:

```sh
sudo pcs-uplink-manager --operator resume --uplink cellular
```

For deliberate session takeover outside the dashboard, use the operator helper
before external `nmcli` actions. Directly re-activating an already active profile
may not produce a new NetworkManager activation identity.

## Usage and API

### Optional trusted Ethernet management

WAN Internet access does not automatically expose PCS services on a WAN.
To permit the existing home-management ports and HTTPS API from a specific
private subnet through the MAC-bound Starlink profile, explicitly run:

```sh
sudo ./scripts/setup-uplink-manager.sh --allow-management-from 192.168.50.0/24
```

This writes `/etc/pcs/uplink-management.json`; additional Ethernet uplinks can
be listed there by configured uplink ID. See the example policy. The helper
resolves the NIC's permanent MAC to its current interface index, so interface
renaming and USB-port changes do not require a new trust rule. It grants access
only while that NIC has an IPv4 address in the approved private subnet. Normal
Starlink subnets receive no permission by default. Source networks must be
private IPv4 /16 or narrower and must not overlap the PCS LAN.

NetworkManager events and firewall reloads refresh only the helper's commented
input rules. Existing LAN/VPN rules, default denies and forwarding policy are
preserved. This allows the same management TCP ports as the existing home-Wi-Fi
policy, plus API port 9443; it does not expose APRS/GPSD or enable discovery on
the WAN. An empty `trusted` list followed by `sudo pcs-uplink-management --apply`
withdraws these permissions. Invalid policy also withdraws these grants.

Firewall permission does not change HTTPS identity. The existing certificate
must cover the hostname or address used by the client. Adding a WAN DHCP address
does not automatically add it to the certificate; use an already-covered name
with appropriate DNS, or separately plan a trusted certificate migration.

The public/admin network displays show download, upload and combined WAN bytes
since boot, with per-uplink breakdowns. These are **interface traffic totals**,
including Pi traffic, forwarded clients, VPN encapsulation and health probes;
they are not carrier billing counters. LAN transfers and tunnel interfaces are
not added again. Upstream-local traffic traversing a WAN NIC is also counted.

`network.uplinks`, `network.usage` and `network.usage_summary` are additive public
fields. Usage displays use decimal MB (1 MB = 1,000,000 bytes), while raw
byte values remain `rx_bytes`, `tx_bytes`, `total_bytes`; per-uplink entries
contain their own `usage`. Authenticated details additionally expose interface,
profile, address and ownership. Public output excludes these identifiers.
The root-writable cache is `/run/pcs-uplink-manager/status.json`; dashboard reads
do not trigger probes. Samples older than 90 seconds become unavailable.

Boot-local accounting survives service restarts, accumulates counter resets and
retains removed-uplink totals. Reboot clears it. Mid-boot initialization or a
counter discontinuity sets `partial`; data lost before observation cannot be
reconstructed. If no data has been collected, display Unavailable, not zero.

## Validation and recovery

See [multi-WAN tests](testing-uplinks.md). Until those live gates are recorded,
local software tests do not establish Starlink, USB NIC or LAN-client behavior.
Installation backups are under `/var/lib/pcs/uplink-backups/`. To return to
operator routing, run setup with `--mode manual`; it restores journaled runtime
metrics/DNS and leaves connected sessions intact. Do not enable the legacy
controller while the generalized controller is installed. Keep LAN recovery
access independent of the WAN being tested.

For full installer rollback, use `sudo ./scripts/setup-uplink-manager.sh
--rollback /var/lib/pcs/uplink-backups/NUMBER` with the backup printed by setup.
The installer also attempts this rollback on installation failure. It restores
saved files/profiles and at most one previous controller. It clears ambiguous
legacy ownership and leaves operator sessions alive.

Optional [Starlink diagnostics](starlink-telemetry.md) use a separate bound connection and never drive uplink selection.
