# Linksys EA4500 OpenWrt AP Setup

The Linksys EA4500 is used in PCS as an OpenWrt-managed access point, Ethernet switch, and bridge device.

It is not the main router in the current PCS design.

The Raspberry Pi is the PCS gateway and service host at:

```text
10.42.0.1
```

The EA4500 OpenWrt management address is:

```text
10.42.0.2
```

## Current Role

The EA4500 provides:

- Wi-Fi access for PCS clients
- Ethernet switching
- LAN bridge between clients and the Raspberry Pi
- OpenWrt management interface

The EA4500 should not provide:

- DHCP
- DNS
- NAT
- PCS firewall routing
- primary WAN/cellular routing

Those functions are handled by the Raspberry Pi.

## Physical Connection

Use a LAN port on the EA4500.

```text
Raspberry Pi eth0 -> EA4500 LAN port
```

The WAN/Internet port is not required for the current PCS layout unless OpenWrt is explicitly configured to include it in the LAN bridge.

## IP Configuration

EA4500 OpenWrt LAN configuration:

```text
LAN IP address:  10.42.0.2
Subnet mask:     255.255.255.0
Gateway:         10.42.0.1
DNS:             10.42.0.1
DHCP server:     disabled
```

The Pi Ethernet interface:

```text
Pi eth0:          10.42.0.1/24
```

Expected PCS clients:

```text
Client IP range:  10.42.0.100 - 10.42.0.200
Gateway:          10.42.0.1
DNS:              10.42.0.1
```

## OpenWrt DHCP

DHCP must be disabled on the EA4500.

The Pi should be the only DHCP server on the PCS LAN.

If DHCP is enabled on both the Pi and EA4500, clients may receive inconsistent network settings.

Symptoms of duplicate DHCP include:

- clients getting wrong gateway
- clients unable to access `10.42.0.1`
- clients unable to resolve DNS
- dashboard not showing clients correctly
- inconsistent behavior after reconnecting Wi-Fi

## OpenWrt Wireless

Configure Wi-Fi normally in OpenWrt.

Recommended settings:

```text
SSID:        Any
Security:   WPA2/WPA3 as supported by client devices
Password:   field-safe shared password
Network:    LAN bridge
```

For maximum compatibility with older laptops, WPA2-Personal may be the safest option.

## OpenWrt LAN Bridge

The OpenWrt LAN bridge should include:

- wired LAN ports used by clients
- Wi-Fi interfaces used by clients
- the port connected to the Raspberry Pi

The exact OpenWrt interface names may vary depending on version/build.

## Management Access

From a PCS client:

```text
http://10.42.0.2
```

From the Pi:

```bash
ping 10.42.0.2
```

Expected:

```text
10.42.0.2 replies
```

## Client Test

From a Windows client connected to the EA4500 Wi-Fi or Ethernet:

```cmd
ipconfig
```

Expected:

```text
IPv4 Address:      10.42.0.x
Subnet Mask:       255.255.255.0
Default Gateway:   10.42.0.1
DNS Server:        10.42.0.1
```

Then test:

```cmd
ping 10.42.0.1
ping 10.42.0.2
```

Expected:

```text
10.42.0.1 replies from the Pi
10.42.0.2 replies from the EA4500
```

Internet tests such as `ping 8.8.8.8` only work when the Pi has an active uplink.

## Important Notes

The EA4500 may show no internet access in OpenWrt or client devices if the Pi cellular/uplink connection is disabled.

That is not necessarily a failure.

PCS is designed to work as an offline LAN server even without internet.
