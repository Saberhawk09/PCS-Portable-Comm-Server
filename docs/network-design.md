# Network Design

PCS uses a Pi-centered network design.

The Raspberry Pi 4 is the main PCS server, LAN gateway, DHCP/DNS provider, file server, NTP server, monitoring host, and optional cellular gateway.

The Linksys EA4500 running OpenWrt is used as a bridge, wireless access point, and Ethernet switch.

## Current Addressing Plan

```text
PCS LAN subnet:          10.42.0.0/24
Raspberry Pi / gateway:  10.42.0.1
OpenWrt AP / switch:     10.42.0.2
DHCP client range:       10.42.0.100 - 10.42.0.200
```

Expected client settings:

```text
IPv4 address:      10.42.0.x
Subnet mask:       255.255.255.0
Default gateway:   10.42.0.1
DNS server:        10.42.0.1
NTP server:        10.42.0.1
```

## Current Physical Topology

```text
Client devices
Windows logging PCs / phones / tablets
        |
        | Wi-Fi or Ethernet
        v
Linksys EA4500 running OpenWrt
Bridge / AP / Ethernet switch
10.42.0.2
        |
        | Ethernet LAN port
        v
Raspberry Pi 4 eth0
10.42.0.1/24
        |
        +-- PCS homepage and authenticated administration
        +-- Samba shares
        +-- Chrony LAN NTP
        +-- GPSD
        +-- DHCP / DNS
        +-- NAT / forwarding
        +-- optional internet uplink
                |
                +-- Wi-Fi uplink during testing
                +-- EM7565 cellular modem when enabled
```

## Why the Pi Is the Network Brain

The Pi handles the core services because PCS is more than just a router.

The Pi needs direct visibility into the LAN so the dashboard and self-test tools can report useful state.

Useful Pi-side visibility includes:

- DHCP leases
- ARP / neighbor table
- Client IP addresses
- Samba session state
- Service health
- GPS/NTP state
- Cellular state
- Storage state
- Backup state

If a separate router handled DHCP and NAT, the Pi could lose visibility into individual clients or see only the router as a single upstream device.

The current design avoids that problem by making the Pi the gateway.

## Role of the Linksys EA4500

The Linksys EA4500 is currently used as:

- OpenWrt-managed access point
- Ethernet switch
- LAN bridge
- field client Wi-Fi device

The EA4500 should not provide these services in the current PCS design:

- DHCP
- DNS
- NAT
- firewall routing for the PCS LAN
- cellular WAN handling

Those roles belong to the Raspberry Pi.

## Ethernet Cabling

Current intended cable path:

```text
Raspberry Pi eth0 -> Linksys EA4500 LAN port
```

The EA4500 WAN port is not required for the PCS layout unless OpenWrt is explicitly configured to bridge that port into the LAN bridge.

Using a LAN port keeps the AP/switch role simple and predictable.

## Internet Uplink Behavior

Internet access is optional.

PCS should still provide local networking, file sharing, local dashboard access, and LAN NTP even without internet.

Possible uplinks:

- Wi-Fi uplink on `wlan0`
- cellular uplink through WWAN modem
- no uplink / offline LAN only

Cellular data is intentionally controlled manually through the PCS Control Panel.
The web panel reports offline-LAN-only operation as a Network-card warning while
keeping overall health `OK - Offline` when local PCS services have no separate
warning or fault. Loss of the `eth0` handoff or `10.42.0.1/24` gateway remains a
network failure rather than being reclassified as intentional offline use.

## Current Service Model

The Pi provides:

- LAN IP: `10.42.0.1`
- DHCP service for PCS clients
- DNS service / forwarding for PCS clients
- NAT / forwarding when an uplink is active
- Samba file shares
- Chrony NTP service
- GPSD input for GNSS-backed time
- PCS public homepage and authenticated control panel
- Legacy port 8080 admin redirect
- Cockpit
- Status and self-test scripts

The OpenWrt AP provides:

- Wi-Fi access
- Ethernet switching
- bridge connectivity to the Pi
- management interface at `10.42.0.2`

## Client Testing

From a Windows client connected to the PCS network:

```cmd
ipconfig
ping 10.42.0.1
ping 10.42.0.2
ping 8.8.8.8
ping google.com
```

Expected:

- `10.42.0.1` replies from the Pi
- `10.42.0.2` replies from the OpenWrt AP
- `8.8.8.8` replies only when an internet uplink is active
- `google.com` resolves only when DNS and an uplink are working

No internet access does not necessarily mean PCS has failed. PCS is designed to remain useful as an offline LAN appliance.

## Client Visibility Checks

On the Pi:

```bash
ip neigh show dev eth0
```

Expected:

```text
10.42.0.x lladdr xx:xx:xx:xx:xx:xx REACHABLE
```

Old `FAILED` entries are usually stale neighbor entries.

They can be cleared with:

```bash
sudo ip neigh flush dev eth0
```

## Related Scripts

Useful scripts from the repository root:

```bash
./scripts/pcs-status.sh
./scripts/pcs-self-test.sh
./scripts/setup-router-wan-share.sh
```

Note: Some script names still use older wording such as `router-wan-share`. In the current design, this refers to the PCS LAN/client handoff path through the Pi Ethernet interface.

On fresh installs, Raspberry Pi OS may create a generated `netplan-eth0` NetworkManager profile. The setup script disables that competing profile and activates `pcs-router-wan-share` so `eth0` comes up as `10.42.0.1/24` with NetworkManager shared IPv4 forwarding.
