# PCS Network Design

PCS uses the Raspberry Pi as the main client network gateway.

The attached router/access point provides Wi-Fi and Ethernet access, while the Pi handles the client network, DHCP, routing, and local services.

## Current Design

```text
Client devices
    ↓ Wi-Fi / LAN
Access point / switch
    ↓ LAN port
Raspberry Pi eth0 - 10.42.0.1/24
    ↓
Raspberry Pi uplink - wlan0 currently, cellular data manual/optional
    ↓
Internet
```

## Client Network

PCS client network:

```text
10.42.0.0/24
```

Pi client-side IP:

```text
10.42.0.1
```

Expected client settings:

```text
IPv4 Address:      10.42.0.x
Subnet Mask:       255.255.255.0
Default Gateway:   10.42.0.1
```

## Access Point / Switch Role

The access point should act as a bridge/switch, not as the main router.

Expected AP/router behavior:

```text
DHCP server: disabled
NAT/routing: disabled or bypassed
Pi connected to AP/router LAN port
```

Current tested AP:

```text
Actiontec T3200
LAN IP: 10.42.0.2
DHCP: disabled
```

## Raspberry Pi Role

The Pi provides:

- Client-side network on `eth0`
- DHCP service for clients
- Routing/NAT to the uplink
- Local Samba shares
- LAN NTP service
- Web dashboard/control panel
- Cockpit access

Current NetworkManager connection:

```text
pcs-router-wan-share
```

The name is historical. It now functions as the PCS client LAN/AP handoff profile.

## Current Uplink

Current tested uplink:

```text
wlan0 → home Wi-Fi/router → internet
```

Future uplink:

```text
cellular modem → internet
```

The client-side network should remain the same when the uplink changes.

## Local Services

PCS services are reachable at:

```text
10.42.0.1
```

Client URLs/services:

```text
PCS Dashboard:      http://10.42.0.1
PCS Control Panel:  http://10.42.0.1:8080
Cockpit:            https://10.42.0.1:9090
Primary Share:      \\10.42.0.1\PCS-Share
Backup Share:       \\10.42.0.1\PCS-Backup
LAN NTP Server:     10.42.0.1
```

## Why AP/Switch Mode Is Preferred

AP/switch mode lets the Pi see real client devices directly on `eth0`.

This improves:

- Connected client display
- Troubleshooting
- Dashboard accuracy
- Simpler routing
- Fewer NAT layers

Useful command:

```bash
ip neigh show dev eth0
```

Example:

```text
10.42.0.232 lladdr 10:6f:d9:d9:71:cf REACHABLE
```

## Older NAT Test Layout

Earlier testing used the router as a NAT router:

```text
Client devices
    ↓
Router Wi-Fi/LAN
    ↓ router WAN
Pi eth0 - 10.42.0.1
```

That worked, but the Pi mostly saw only the router WAN device instead of individual clients.

The AP/switch layout is preferred for PCS.

## Troubleshooting

Check Pi network state:

```bash
nmcli device status
ip -brief addr
ip route
```

Check visible clients:

```bash
ip neigh show dev eth0
```

Check internet from Pi:

```bash
ping -c 3 8.8.8.8
ping -c 3 google.com
```

Check from Windows client:

```cmd
ipconfig
ping 10.42.0.1
ping 8.8.8.8
ping google.com
```
