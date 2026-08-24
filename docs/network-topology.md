# Network Topology

This document describes the current PCS network topology.

PCS uses a Raspberry Pi 4 as the central server/gateway and a Linksys EA4500 running OpenWrt as the access point and Ethernet switch.

## Current Topology

```text
Client devices
Windows logging PCs / laptops / phones
        |
        | Wi-Fi / Ethernet
        v
Linksys EA4500 running OpenWrt
AP / bridge / switch
10.42.0.2
        |
        +-- Pi-Star hotspot
        |   10.42.0.3
        |
        | Ethernet LAN
        v
Raspberry Pi 4
PCS server / gateway
10.42.0.1
        |
        +-- Samba file sharing
        +-- Chrony LAN NTP
        +-- GPSD
        +-- PCS public homepage
        +-- authenticated admin control panel
        +-- DHCP / DNS
        +-- NAT / forwarding
        +-- optional internet uplink
                |
                +-- Wi-Fi uplink
                +-- EM7565 cellular modem
```

## IP Address Plan

```text
PCS subnet:             10.42.0.0/24
Raspberry Pi:           10.42.0.1
Linksys EA4500 OpenWrt: 10.42.0.2
Pi-Star hotspot:        10.42.0.3
DHCP clients:           10.42.0.100 - 10.42.0.200
```

## Device Roles

| Device | Role | Address |
| --- | --- | --- |
| Raspberry Pi 4 | PCS server, gateway, DHCP, DNS, Samba, NTP, dashboard | 10.42.0.1 |
| Linksys EA4500 | OpenWrt AP, bridge, Ethernet switch | 10.42.0.2 |
| Pi-Star hotspot | Digital voice hotspot and PCS NTP/GPSD client | 10.42.0.3 |
| Field clients | Logging PCs, phones, tablets | 10.42.0.x |
| EM7565 | Cellular modem and GNSS source | managed by Pi |

## Raspberry Pi Role

The Pi is the center of PCS.

It provides:

- LAN gateway
- DHCP
- DNS
- optional NAT to internet uplink
- Samba file sharing
- GPSD
- Chrony NTP
- RTC support
- PCS public homepage and authenticated administration
- legacy port 8080 admin redirect
- status/self-test scripts
- backup sync tools

## OpenWrt AP Role

The Linksys EA4500 provides:

- Wi-Fi access for clients
- Ethernet switching
- LAN bridge to the Pi
- management interface at `10.42.0.2`

It should not provide DHCP in the current PCS design. The Pi should be the only DHCP server on the PCS LAN.

## Cellular / Internet Role

The EM7565 is connected to the Pi through a USB WWAN enclosure.

Cellular data is optional and can be manually controlled or selected as the
automatic fallback for unavailable Wi-Fi. PCS should continue to provide local
services even when both uplinks are disconnected or unavailable.

## GPS / Time Role

The WWAN modem can provide GNSS NMEA data to the Pi.

Expected time chain:

```text
EM7565 GNSS NMEA
        |
        v
gpsd
        |
        v
Chrony preferred GPS source
        |
        +-- public Internet NTP fallback
        |
        +-- RTC-seeded system-clock holdover when neither is usable
        |
        v
LAN clients using 10.42.0.1 as NTP server
```

The Raspberry Pi RTC provides a guarded boot-time seed before GPS or Internet
time is available. It is not a continuously sampled Chrony source; synchronized
GPS/Internet time is written back through `rtcsync`, and Chrony advertises the
seeded system clock at degraded stratum 10 only when neither authoritative
source is selectable.

The Pi-Star hotspot uses `10.42.0.1` as its preferred NTP server. PCS can also
share the WWAN modem's GNSS data with trusted field-LAN devices through an
optional LAN-only GPSD proxy:

```text
EM7565 NMEA -> gpsd on 127.0.0.1:2947
                    |
                    v
       LAN proxy on 10.42.0.1:2947
                    |
                    +-- Pi-Star YSFGateway at 10.42.0.3
                    +-- Other trusted GPSD/NMEA clients
```

The proxy binds only to the PCS LAN address so live location data is not
exposed on WWAN or other uplink interfaces. See
[GPS Network Sharing](gps-network-sharing.md) for native GPSD and raw-NMEA
client examples. Pi-Star's legacy UDP port 7834 MobileGPS path is for its local
serial-GPS helper; it is not used for raw NMEA forwarding from PCS.

## Storage Role

PCS provides two Samba shares:

```text
\\10.42.0.1\PCS-Share
\\10.42.0.1\PCS-Backup
```

Current storage mapping:

```text
PCS-Share   -> /mnt/pcs-usb/PCS-Share
PCS-Backup  -> /srv/pcs-share-backup
```

`PCS-Share` is the primary removable USB field share.

`PCS-Backup` is the SD-card backup mirror.

## Normal Client Access

From a PCS client:

```text
PCS Homepage:    http://10.42.0.1/
Admin Login:     http://10.42.0.1/admin/
Cockpit:         https://10.42.0.1:9090
Primary Share:   \\10.42.0.1\PCS-Share
Backup Share:    \\10.42.0.1\PCS-Backup
NTP Server:      10.42.0.1
OpenWrt AP:      http://10.42.0.2
```

## Connected Client Visibility

With the access point in AP/switch mode, the Pi can see clients directly on `eth0`.

Useful command:

```bash
ip neigh show dev eth0
```

Old or inactive clients may show as `FAILED`. These are usually stale neighbor entries.

They can be cleared with:

```bash
sudo ip neigh flush dev eth0
```

## Friendly Client Names

The PCS dashboard can use a local friendly-name map for known clients.

Tracked example file:

```text
config/local-client-names.example.tsv
```

Ignored local file:

```text
config/local-client-names.tsv
```

Example format:

```text
10.42.0.2	Linksys EA4500 OpenWrt AP
10.42.0.123	Field Laptop
aa:bb:cc:dd:ee:ff	Field Laptop
```

The local file is ignored so private device names and MAC addresses are not pushed to GitHub.

## Failure Behavior

PCS should degrade gracefully.

| Failure | Expected behavior |
| --- | --- |
| Cellular unavailable | LAN, Samba, dashboard, and local NTP continue |
| GPS unavailable | Chrony may use RTC, system clock, or available fallback sources |
| USB share unavailable | Backup share should still be available |
| OpenWrt AP unavailable | Pi services still run, but clients may lose Wi-Fi/switch access |
| Pi unavailable | PCS core services are unavailable |

## Troubleshooting

Check Pi network devices:

```bash
nmcli device status
ip -brief addr
ip route
```

Check visible clients:

```bash
ip neigh show dev eth0
```

Check internet from the Pi:

```bash
ping -c 3 8.8.8.8
ping -c 3 google.com
```

Check from Windows:

```cmd
ipconfig
ping 10.42.0.1
ping 10.42.0.2
ping 8.8.8.8
ping google.com
```

## Notes

This topology replaces the older design where the Pi fed the WAN port of a router.

The current architecture is better for PCS because the Pi can see clients directly and provide dashboard/status visibility.
