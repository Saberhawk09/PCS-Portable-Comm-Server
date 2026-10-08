# Managed-switch VLAN migration

Status: development; deployment is not authorized by this document. No migration
has been run against PCS. Legacy remains the default. Pulling this branch,
reinstalling PCS, and restarting services do not select VLAN mode. Hardware
acceptance requires the actual DGS-1100-08V2 revision and firmware.

## Topology and security boundaries

| Port | Connection | VLAN membership | PVID |
|---|---|---|---|
| 1 | Pi native eth0 | tagged 10 and 20 | unused isolated VID, if supported |
| 2 | Starlink or external WAN | untagged 20 only | 20 |
| 3 | Brick LAN port | untagged 10 only | 10 |
| 4 | Existing unmanaged client switch | untagged 10 only | 10 |
| 5 | Pi-Star Ethernet | untagged 10 only | 10 |
| 6–8 | Unused | disabled, or separate isolated unused VLANs | isolated |

The Pi routes; the D-Link only switches. Physical eth0 has no IPv4 or IPv6
address. eth0.10 owns 10.42.0.1/24, DHCP 10.42.0.100–10.42.0.200, DNS and NAT.
eth0.20 is a DHCP WAN. The four external clients share the unmanaged switch's
single Gigabit uplink; local traffic can remain on that switch. A single Pi
Gigabit full-duplex link carries both VLANs, so measured aggregate throughput
must account for routing, direction, CPU and client contention.

The root-owned `/etc/pcs/network-mode` contains `legacy` or `vlan`; absence means
legacy. Invalid contents stop interface-sensitive scripts. Only supervised
apply writes VLAN mode. LAN selection is a closed choice between eth0 and
eth0.10, never a wildcard parent match. WAN identity requires interface eth0.20,
parent eth0, tag 20, type vlan and the exact saved/applied connection UUID.
Parent device type and active session are checked again before runtime changes.
Physical USB Ethernet MAC binding remains supported. Starlink retains its
configured ID/name, telemetry association, priority and failover timing.

The dedicated inet firewall rejects parent ingress, unsolicited WAN ingress and
WAN-to-LAN forwarding. DHCP replies and necessary IPv6 control traffic remain
possible. Existing WireGuard, API, KISS and discovery firewalls select the trusted
LAN. Explicit trusted-home grants are reapplied to the VLAN guard as well as the
service guards; RFC1918 source policy and current uplink subnet checks remain.
No home grant is inferred from a shared MAC address.

Brick management IPv4 10.42.0.2 and approved switch IPv4 10.42.0.4 cannot route
through the Pi. Their **observed management MAC addresses are mandatory** for
staging; MAC-based inet rules cover IPv6 and alternate IPv4 addresses. These
identities also cannot initiate connections to Pi proxies such as DNS. Replies
to Pi-initiated management connections are permitted. Ordinary bridged Wi-Fi
clients retain their own source MACs and addresses and are unaffected. Verify
this on the actual Brick. If it rewrites all client MACs, changes its management
identity, or routes/NATs clients, stop commissioning. Identity filtering cannot
contain arbitrary deliberate MAC spoofing; use a trustworthy AP if that threat
must be addressed. Local L2 traffic between LAN clients does not traverse the Pi.

LAN routed IPv6 is disabled; WAN IPv6 preserves the existing wired profile's
`auto`, `ignore`, or `disabled` setting. Other existing IPv6 policies are refused
for explicit review. No cellular connection is restarted by the migration.

## D-Link preparation, with upstream WAN unplugged

Use an isolated laptop and switch. Do not attach the Pi trunk or any upstream
WAN while the switch is factory default: all ports may be bridged. Pi software
cannot repair that physical isolation failure.

Record the label's exact model, hardware revision, serial and installed firmware
from Device Information. Obtain the matching regional manual, release notes and
firmware from [D-Link support](https://support.dlink.com/resource/products/DGS-1100-08V2/REVA/).
Do not cross-flash another revision or the PoE model. A supervised firmware
update is a separate operator action, with configuration backup and stable power.

The [D-Link small-port V2 manual](https://ftp.dlink.de/dgs/dgs-1100-v2/documentation/DGS-1100V2_5-8port_man_reva_Manual_en.pdf)
documents factory 10.90.90.90/8. Confirm against the unit; set the isolated
laptop to e.g. 10.90.90.91/8 without a gateway and browse the switch. Set a unique
administrator password immediately. Record it privately.

In L2 Features / VLAN, enable 802.1Q and create VLANs 10 and 20. Apply the table
above, then set PVIDs. Disable asymmetric, voice and surveillance VLAN features.
Remove VLAN 1 membership from production ports. Where available, select
admission of tagged frames on port 1, untagged frames on access ports, and
ingress filtering. If no tagged-only control exists, assign unused native
traffic to an isolated unused VID with no WAN/LAN membership.

Enable management VLAN 10 if the actual firmware offers it. Using an isolated
LAN test port, prove management is reachable from VLAN 10 and unreachable from
VLAN 20, including with a manually assigned same-subnet test address. Do not
assume that merely setting 10.42.0.4 restricts management by VLAN. If secure
management isolation is unavailable, stop and revise the design before deployment.

Check 10.42.0.4 against the operator inventory, DHCP leases, neighbor records and
an authorized duplicate-address probe. Approval and collision checking are
required; this implementation uses fixed .4. Give the switch no default gateway
or DNS server. Set the Brick to .2, DHCP/NAT off, with only a LAN-port uplink.

Use Save / Save Configuration to persist the settings, then export a private
configuration backup. Remove all switch power, reconnect and verify the entire
port/VLAN/PVID/management configuration. Repeat WAN/LAN isolation with synthetic
clients and DHCP servers before attaching any live WAN. The manual documents
Configuration Backup/Restore and Reset under Tools; verify the model-specific
physical reset procedure before relying on it. Restore only with WAN unplugged.
Reject this deployment if isolation is lost after complete power removal.

## Read-only prerequisites and staging

The target must already have the existing uplink manager and a commissioned
physical `starlink` Ethernet entry with a UUID. The migration preserves its
name, rank and activation policy, and every other uplink row. Unbound Ethernet
profiles are refused because they could capture the trunk at boot. Bind those
profiles explicitly during a separately authorized preparation step.

NetworkManager **1.52 or newer** is required for the explicit shared DHCP range;
see the [NetworkManager property documentation](https://networkmanager.dev/docs/libnm/latest/NMSettingIPConfig.html).
An older target fails preflight without changing its network. Do not upgrade
NetworkManager on a commissioned appliance as an incidental migration step.
The nftables sharing backend, systemd, existing PCS services and Python 3 are
required. No extra router or network-management daemon is installed.

After separate approval to inspect the target, from the reviewed checkout:

```sh
sudo ./scripts/setup-pcs-vlan-switch.sh --preflight
nmcli --version
nmcli -g ipv6.method connection show UUID_OF_EXISTING_STARLINK_PROFILE
```

Staging can run on a development computer with Python and synthetic fixture
MACs for tests. For commissioning, substitute the measured identities and
existing WAN IPv6 method; the examples below intentionally have placeholders.
Keep the stage and backups outside Git and protect their contents.

```sh
python3 scripts/pcs_vlan_switch.py --stage --directory /var/tmp/pcs-vlan-stage \
  --brick-mac BRICK_MANAGEMENT_MAC --switch-mac SWITCH_MANAGEMENT_MAC \
  --wan-ipv6 auto
python3 scripts/pcs_vlan_switch.py --dry-run --directory /var/tmp/pcs-vlan-stage
```

Stage only writes its output directory. It does not call nmcli, load profiles,
change autoconnect, start services, or touch the modem. It creates three native
NM keyfiles with stable UUIDs and autoconnect=false, plus the guard and a digest
manifest. Apply regenerates and compares all staged content before use. Stage
is not proof that target libnm accepts the files; CI separately verifies that.

## Supervised apply: only after explicit deployment authorization

Have a tested local console, original LAN cabling, the USB WAN adapter, switch
backup and a second LAN test client ready. Do not use a sole SSH session as the
recovery path. Freeze concurrent network-policy edits through this transaction.
Copy the reviewed complete checkout to PCS without running the base installer.
Confirm switch power-cycle isolation, .4 approval, identity records and fallback
cabling. Record current profile/service/routing state privately.

```sh
sudo ./scripts/setup-pcs-vlan-switch.sh --apply \
  --directory /var/tmp/pcs-vlan-stage --timeout 1800 \
  --authorize-network-change --switch-isolation-verified
```

These flags are explicit operator attestations, not authorization granted by
this document. Apply snapshots NM profiles, PCS policy/runtime files, modified
helpers, active connections/services and firewall into a timestamped root-only
`/var/lib/pcs/vlan-switch/backup-*` directory. It arms the persistent rollback
service/timer **before** modifying networking. Recovery code is copied into the
same root-owned state directory, independent of SSH and the source checkout.
The timer survives logout and reboots; after reboot it has a bounded timeout.
Commit still rejects an expired original wall-clock deadline.

Apply installs the guard before bringing up VLANs, disables competing parent
and old WAN autoconnect, preserves their profiles, starts the address-free parent
and shared LAN, and requests the WAN asynchronously. LAN startup does not depend
on WAN DHCP. It restarts the uplink observer and previously active firewall
helpers, never ModemManager or a cellular profile. Record the printed backup
path. Rewire eth0 to port 1, LAN devices to their access ports, and only then
attach the verified WAN. The original USB WAN stays available for fallback.

```sh
sudo ./scripts/setup-pcs-vlan-switch.sh --check
systemctl status pcs-vlan-rollback.timer
nmcli -f NAME,UUID,TYPE,DEVICE,AUTOCONNECT connection show
ip -d link show eth0.10
ip -d link show eth0.20
ip -brief address
ip -4 route show table all
ip -6 route show table all
sudo nft list ruleset
sudo nft list table ip nm-shared-eth0.10
sudo nft list table inet pcs_vlan_guard
./scripts/pcs-self-test.sh
```

Expect exact tag/parent identities, 10.42.0.1/24 only on eth0.10, no parent
addresses, no LAN default route, no overlapping WAN subnet, the intended WAN
metrics/DNS priorities, and NM shared NAT/forwarding on eth0.10. NM's generated
table name is checked on the target; missing/different tables stop acceptance.

## Acceptance witnesses and commit

Record commands, timestamps and results, not just check marks:

| Gate | Required independent evidence |
|---|---|
| LAN with WAN unplugged | New client lease in .100–.200 from .1; DNS and NTP available; dashboard and Samba usable |
| VLAN 20 only | No LAN DHCP lease, DNS, API, SSH, KISS, GPSD, SMB or discovery exposed from WAN |
| Broadcast separation | Controlled DHCP server on WAN cannot lease to LAN; LAN server never leases to WAN; repeat IPv6 RA tests |
| WAN routing | Non-overlapping DHCP address, DNS and IPv4/IPv6 routes; real Starlink and home-WAN access |
| Services | APRS/KISS, GPSD 2947, NTP, Samba, discovery, dashboard and API work only for intended clients |
| WireGuard | Approved management works; LAN-initiated tunnel access and unapproved peers remain denied |
| Brick and switch | IPv4 and IPv6 egress attempts from observed management identities fail and increment guard counters; ordinary Wi-Fi clients succeed |
| Failover | Disconnect WAN, observe existing hysteresis/fallback without restarting modem; reconnect and verify preferred recovery, display, telemetry and per-uplink bytes |
| DHCP outage | Remove/recover only the test upstream DHCP service; LAN remains usable and wired DHCP eventually recovers |
| Reboot | Reboot Pi within watchdog window; VLANs and LAN return without WAN; then test full switch power loss and persistent isolation |
| Repetition | Repeat check and apply after commit; profiles and guard rules are not duplicated |
| Rollback drill | Let watchdog expire under supervision, verify saved files/routes/firewall/services, rewire legacy and confirm client service |

Use `dig @10.42.0.1 example.org`, `chronyc sources -v`, a fresh client DHCP lease,
`nc -vz 10.42.0.1 PORT`, and packet capture on both VLANs as appropriate. Obtain
separate authorization for live WAN interruption and any RF exercise; this code
change does not authorize either. Namespace CI uses synthetic packets only.

For performance, run `iperf3 -s` on a supervised LAN peer and `iperf3 -c PEER -P 4`
(and `-R`) from PCS and multiple external clients. For routed throughput, place a
controlled iperf3 endpoint beyond VLAN 20 and measure from LAN in both directions.
Then record actual Starlink and home-WAN speed tests, CPU load and packet loss.
The prior **188/218 Mbps USB 2.0 measurement is a baseline**, not a new expected
speed. No Gigabit result is claimed until measured. Finally cold-boot with the
USB 3.0 WAN adapter disconnected and measure time to a fresh GNSS fix; preserve
satellite/fix evidence and Wi-Fi association results. A cached fix is insufficient.

Create a private JSON receipt using the actual backup path and evidence references:

```json
{
  "backup": "/var/lib/pcs/vlan-switch/backup-REPLACE",
  "operator": "REPLACE",
  "lan_dhcp_dns_ntp": "timestamp and client evidence file",
  "wan_isolation_ipv4_ipv6": "timestamp and captures",
  "brick_switch_egress": "identity records, captures and counters",
  "services_wireguard": "service and peer test results",
  "failover_recovery": "authorized trial and status evidence",
  "pi_reboot": "boot IDs and post-boot checks",
  "switch_cold_boot": "power-cycle VLAN, PVID and isolation evidence"
}
```

Only after those gates pass, before the original deadline:

```sh
sudo ./scripts/setup-pcs-vlan-switch.sh --commit --receipt /root/pcs-vlan-acceptance.json
```

Commit reruns Pi checks, records the explicit external witness, and cancels the
timer. SSH success alone never commits. Keep the USB adapter until throughput,
GNSS and full field acceptance are complete.

## Recovery and troubleshooting

```sh
# Pending migration, from local console or the independent watchdog:
sudo ./scripts/setup-pcs-vlan-switch.sh --rollback
# Explicit recovery after commit, using the recorded backup:
sudo ./scripts/setup-pcs-vlan-switch.sh --rollback \
  --backup /var/lib/pcs/vlan-switch/backup-REPLACE
```

Rollback restores the snapshot and previous active Ethernet profiles. Existing
cellular sessions are left connected; it never activates cellular. It restores
PCS/NM sharing tables without flushing unrelated firewall tables. Backups are
retained and must remain private. Do not restore an old backup over unrelated
subsequent policy changes without review.

Pi rollback cannot change the switch configuration or cabling. Disconnect WAN,
return eth0 to the original untagged LAN and USB Ethernet to WAN. If necessary,
restore the switch from its private known-good export while isolated. Never
factory-reset a switch with live WAN and LAN connected. If the observer reports
VLAN identity errors, inspect UUID/type/parent/tag rather than weakening guards.
If DHCP range preflight fails, stop: the installed NM lacks the required property.
If no `nm-shared-eth0.10` table appears, investigate NM backend/profile activation.
If management MACs differ or .4 collides, restage only after resolving the identity.

The existing hard-coded FQ WAN-fault/Windows witness harness is not a VLAN
commissioning tool. Do not run legacy fault-injection scenarios against this
new topology; use the supervised tests above until those scenarios have their
own topology qualification. Pi-Star's setup script still targets Pi-Star's own
physical eth0, intentionally. Physical parent identity protections remain eth0.
