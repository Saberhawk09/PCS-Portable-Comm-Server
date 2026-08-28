# WireGuard Remote Management

## Status and deployment boundary

This feature was commissioned on PCS on 2026-08-26. The supervised deployment
proved cellular startup, IPv4 DDNS endpoint refresh, authenticated handshake,
firewall isolation, unchanged default routing, reboot recovery, desktop-peer
SSH/HTTP access, and routed HTTP/ICMP access to the PCS host, OpenWrt, and
Pi-Star. The private profile and real peer details remain outside Git. The base
installer keeps WireGuard explicit and default-off for future reinstallations.

PCS acts as an outbound WireGuard client. It is not an Internet-facing VPN
server. The tunnel is a split-tunnel management path and does not replace the
normal Wi-Fi or cellular default route.

## Security objectives

The design enforces these invariants:

- PCS never installs `0.0.0.0/0`, `::/0`, or a home-LAN prefix through
  WireGuard.
- Every PCS-side `AllowedIPs` entry is an explicit IPv4 `/32` management peer.
- The home hub or another explicitly listed WireGuard `/32` is the only remote
  source allowed to administer PCS.
- Trusted management sources may reach the PCS host and, when deliberately
  routed, `10.42.0.0/24` devices such as OpenWrt and Pi-Star.
- PCS LAN clients cannot initiate traffic into `wg-pcs`.
- WireGuard cannot forward remote clients to a PCS WAN interface or turn PCS
  into an exit node.
- SSH, HTTP/HTTPS, Samba, the legacy admin redirect, and Cockpit are accepted
  from the local PCS LAN, an explicitly configured private home-Wi-Fi subnet,
  and trusted WireGuard sources, then dropped on cellular and other ingress
  interfaces while the feature firewall is active.
- A tunnel activation without an authenticated home-hub handshake is rolled
  back automatically.
- WireGuard does not start cellular or alter the selected manual/automatic
  cellular policy.
- If a hostname endpoint cannot resolve during an offline boot, an enabled
  tunnel is retried only after NetworkManager reports that an operator-selected
  uplink became available.
- Hostname endpoints are refreshed through IPv4 at activation, on uplink
  changes, and every five minutes. This avoids an unusable DDNS IPv6 result on
  uplinks where the home router's WireGuard listener is reachable only by IPv4.
- Private keys and actual hub details remain outside Git.

```text
Authorized home admin
        |
        | home-side route + hub firewall + SNAT to 10.77.0.1
        v
Home WireGuard hub (public endpoint, 10.77.0.1/32)
        |
        | encrypted outbound-initiated tunnel
        v
PCS wg-pcs (example 10.77.0.20/32)
        |
        +-- PCS host services
        |
        +-- explicitly permitted forwarding to 10.42.0.0/24

PCS LAN 10.42.0.0/24 --X--> wg-pcs/home network
```

When PCS is connected to the operator's home Wi-Fi, direct management does not
require WireGuard. Set `PCS_WG_HOME_INTERFACE=wlan0` and the exact private home
subnet, such as `PCS_WG_HOME_NETWORK=192.168.50.0/24`, in the deployment-local
runtime policy. Both values are empty after profile import because a VPN export
cannot safely identify the operator's home LAN. This exception opens only the
protected host-management ports from that subnet; it does not trust cellular,
install a home-LAN route through WireGuard, or permit PCS-LAN clients to exit
through the tunnel.

WireGuard cryptographic `AllowedIPs`, PCS nftables policy, and the home-hub
firewall are separate controls. All three are required.

## Home-hub trust models

The conservative SNAT model accepts only the hub's WireGuard address, such as
`10.77.0.1/32`. An authorized home administrator may have any unrelated local
address. A capable hub source-NATs that administrator's packets to its own
WireGuard address before sending them to `10.42.0.0/24`.

PCS therefore does not need a route to the home LAN and never sees its address
range. This also avoids granting all home devices access merely because they
share a subnet. The home hub must allow only the intended administrator host
or an equally narrow trusted source before applying SNAT.

Stock ASUS firmware did not forward ordinary home-LAN clients into this server
tunnel during commissioning. PCS therefore uses the alternate supported model:
the hub address and each approved administrator device are listed as individual
WireGuard `/32`s. No management `/24` or home-LAN prefix is trusted, and all
other ASUS VPN peers remain blocked by the PCS firewall.

## Local repository components

| File | Purpose |
| --- | --- |
| `scripts/setup-pcs-base.sh` | optional, default-off profile import followed by handshake-gated activation |
| `scripts/setup-wireguard-management.sh` | profile import, preparation, key generation, validation, inactive configuration, activation, checks, deactivation, and rollback |
| `scripts/pcs_wireguard_profile.py` | rejects unsafe or incompatible `wg-quick` profile contents without printing secrets |
| `scripts/pcs-wireguard-firewall.sh` | fail-closed host/forward isolation and NetworkManager shared-LAN compatibility |
| `scripts/pcs-wireguard-endpoint-refresh.sh` | IPv4-only DDNS endpoint refresh without route or DNS-policy changes |
| `systemd/pcs-wireguard-firewall.service` | installs isolation before `wg-quick@wg-pcs` |
| `systemd/pcs-wireguard-endpoint-refresh.service` | applies the current IPv4 DDNS result to the active peer |
| `systemd/pcs-wireguard-endpoint-refresh.timer` | repeats endpoint refresh every five minutes |
| `networkmanager/90-pcs-wireguard-firewall` | restores compatibility rules after NetworkManager rebuilds its shared-LAN table |
| `config/pcs-wireguard-management.example.conf` | fake, non-secret configuration contract |

The base installer uses these components only when the operator explicitly
answers yes. `DEFAULTS` mode selects no. A missing, exposed, malformed, or
unsafe profile is rejected before components are installed. Any later import,
activation, handshake, or check failure disables the services and rolls back
the installed feature files before the rest of base setup continues.

## Profile-based base setup

Before starting the base installer, export a normal WireGuard client profile
from the already-configured home hub and place it at this exact repository
path:

```text
private-config/wg-pcs.conf
```

That path and `wg*.conf` are ignored by Git. The profile contains a private key,
so keep it mode `0600` and owned by the normal Pi user:

```bash
mkdir -p private-config
chmod 700 private-config
chmod 600 private-config/wg-pcs.conf
```

The accepted profile is deliberately narrower than general `wg-quick` syntax:

```ini
[Interface]
Address = 10.77.0.20/32
PrivateKey = PCS_PRIVATE_KEY_FROM_THE_HOME_HUB_CLIENT_EXPORT
DNS = 10.77.0.1

[Peer]
PublicKey = HOME_HUB_PUBLIC_KEY
PresharedKey = OPTIONAL_ASUS_PRESHARED_KEY
Endpoint = vpn.example.net:51820
AllowedIPs = 10.77.0.1/32
PersistentKeepalive = 25
```

It must have exactly one interface and one peer. `Address` must be one IPv4
`/32`. Every `AllowedIPs` value must be another `/32` in the same management
`/24`; those values become the explicit trusted administration sources. Do not
put the PCS LAN, home LAN, `0.0.0.0/0`, or `::/0` in this file.

The importer supports the `DNS` and `PresharedKey` lines emitted by ASUS. An
ASUS DNS address is accepted only when it is one IPv4 address inside the
management `/24`, and is then deliberately discarded so PCS retains its normal
DNS policy. The PSK must be a valid 32-byte WireGuard key; it is extracted into
`/etc/pcs/wireguard/preshared.key` as root-owned mode `0600` and is rendered
only into the protected runtime tunnel configuration.

The importer still rejects `PreUp`, `PostUp`, `PreDown`, `PostDown`, `Table`,
`MTU`, additional sections/peers, broad routes, symlinks, and group/world
readable files. This prevents an imported profile from executing shell hooks
or changing DNS/routing policy. It never prints the private key or PSK.

Run the base installer normally and answer yes to the WireGuard question. A
different ignored profile path may be entered at the prompt. The home hub must
already recognize the profile's PCS public key and be reachable: activation
must obtain a real handshake within the bounded timeout or it is rolled back.

## PCS configuration contract

For a reinstall or replacement profile, copy the example to the
deployment-local location during a supervised maintenance session:

```bash
sudo install -d -o root -g root -m 0755 /etc/pcs
sudo install -o root -g root -m 0644 \
  config/pcs-wireguard-management.example.conf \
  /etc/pcs/wireguard-management.conf
sudoedit /etc/pcs/wireguard-management.conf
```

Replace the fake hub public key and endpoint. Keep these constraints:

```text
PCS_WG_ADDRESS             one dedicated management IPv4 /32
PCS_WG_ALLOWED_IPS         explicit /32 values in the PCS address's management /24 only
PCS_WG_ADMIN_SOURCES       subset of PCS_WG_ALLOWED_IPS
PCS_WG_INTERFACE           wg-pcs
PCS_WG_LAN_INTERFACE       eth0
PCS_WG_LAN_NETWORK         10.42.0.0/24
PCS_WG_MTU                 1280, fixed for reliable cellular-path TLS and SSH
PCS_WG_PRIVATE_KEY_FILE    root-only file outside Git
PCS_WG_USE_PRESHARED_KEY   yes only when the imported peer supplies one
PCS_WG_PRESHARED_KEY_FILE  separate root-only file outside Git
```

The setup tool rejects broad prefixes, default routes, PCS-LAN overlap,
management peers outside the tunnel `/24`, unapproved interfaces, MTU values
other than the cellular-safe 1280-byte setting, weakened protected-port sets,
and malformed public keys/endpoints.

## Home-hub peer requirements

Configure the home hub before activating PCS. Its WireGuard peer for PCS needs
the PCS public key and these routes:

```ini
[Peer]
PublicKey = PCS_PUBLIC_KEY_FROM_GENERATE_KEY
AllowedIPs = 10.77.0.20/32, 10.42.0.0/24
```

The first entry routes the PCS tunnel address. The second routes the PCS field
LAN to that peer. It does not grant access by itself; the hub firewall must
still restrict who can forward to that destination.

The exact home firewall depends on the hub platform. Its policy must implement
the following logic with real interface names and the one authorized source:

```text
authorized admin -> 10.42.0.0/24 via hub WireGuard       ACCEPT
related return traffic from 10.42.0.0/24                 ACCEPT
all other home/WireGuard clients -> 10.42.0.0/24         DROP
PCS or 10.42.0.0/24 -> home LAN                          DROP
authorized admin -> 10.42.0.0/24 leaving hub WireGuard  SNAT to 10.77.0.1
```

Do not copy a generic permissive forwarding example. Capture and review the
actual home-hub rules during deployment. If an Android phone connects as its
own peer to the home hub and the hub routes its management address without
SNAT, add only that phone's WireGuard `/32` to the PCS allowed/admin lists and
enforce the same source rule on the hub.

The authorized home device also needs a route for `10.42.0.0/24` through the
hub. Prefer a route on that one device. A home-router-wide static route is
broader and requires equally strong hub filtering.

## Standalone supervised PCS workflow

These commands are for supervised reinstall, recovery, or validation. Do not
rerun mutating commands on the commissioned PCS without an approved maintenance
task.

```bash
./scripts/setup-wireguard-management.sh --validate-profile private-config/wg-pcs.conf
./scripts/setup-wireguard-management.sh --prepare
./scripts/setup-wireguard-management.sh --import-profile private-config/wg-pcs.conf
./scripts/setup-wireguard-management.sh --validate-config
./scripts/setup-wireguard-management.sh --generate-key
./scripts/setup-wireguard-management.sh --configure
./scripts/setup-wireguard-management.sh --help
```

`--validate-profile` performs a read-only preflight. `--import-profile` accepts
only the restricted format above and installs the private key as root-owned
mode `0600`. When present, the PSK is installed separately with the same
protection; an ASUS DNS entry is not applied. All services remain inactive.
`--prepare` installs inactive
components only. `--configure` writes the root-only `wg-quick` file but refuses
to replace an active or boot-enabled
tunnel. Neither command changes the running firewall or starts WireGuard.

After adding the printed PCS public key and PCS routes to the home hub:

```bash
./scripts/setup-wireguard-management.sh --activate
./scripts/setup-wireguard-management.sh --check
```

Activation requires an explicit confirmation, installs the isolation firewall
first, proves that no default route uses `wg-pcs`, waits at most 45 seconds for
an authenticated handshake, and rolls back activation if the handshake never
appears.

## Deployment validation matrix

Do not call the feature deployed until every applicable row is demonstrated.

| Test | Required result |
| --- | --- |
| No WAN | PCS LAN remains operational; WireGuard has no handshake and a hostname endpoint may remain inactive with a warning |
| Wi-Fi WAN | tunnel handshakes without changing the default route |
| Manually started cellular | tunnel recovers through CGNAT |
| Manually stopped cellular | WireGuard does not reconnect cellular |
| Home admin to PCS WireGuard address | SSH/admin/Cockpit work |
| Home admin to `10.42.0.2` and `10.42.0.3` | works only for the authorized source |
| Ordinary PCS Wi-Fi client to management `/32` | blocked |
| Ordinary PCS Wi-Fi client to home LAN | no route and blocked |
| Unauthorized home client to PCS | blocked at the hub |
| PCS to public Internet | continues through ordinary Wi-Fi/cellular path |
| NetworkManager `eth0` down/up | dispatcher restores the narrow compatibility rules |
| Reboot with WAN | firewall starts before tunnel and checks pass |
| Reboot without WAN | PCS local services boot normally; no disruptive retry loop; later uplink availability passively retries the enabled tunnel |

Also run the full PCS self-test and inspect `ip route`, `ip rule`, `wg show`,
and `nft list ruleset`. A successful handshake alone is not proof of isolation.

## Deactivation and rollback

To stop the feature but retain all inputs and key material:

```bash
./scripts/setup-wireguard-management.sh --deactivate
```

To remove installed feature/runtime files while preserving the deployment
config, private key, and any pre-shared key for recovery:

```bash
./scripts/setup-wireguard-management.sh --rollback
```

Neither operation removes packages. Rollback reports exactly which sensitive
inputs remain. Delete or rotate preserved key material only as a separate
deliberate credential-management action.

## References

- [WireGuard quick start](https://www.wireguard.com/quickstart/)
- [`wg-quick(8)` routing behavior](https://man7.org/linux/man-pages/man8/wg-quick.8.html)
- [NetworkManager shared-mode firewall backend](https://www.networkmanager.dev/docs/api/latest/NetworkManager.conf.html)
- [nftables connection tracking](https://wiki.nftables.org/wiki-nftables/index.php/Matching_connection_tracking_stateful_metainformation)
