# PCS Network Topology

PCS uses the Raspberry Pi as the main gateway for client devices.

The access point/router is currently used as a Wi-Fi access point and Ethernet switch. The Pi handles the client network, DHCP, routing, and internet handoff.

## Current Tested Layout

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

## Raspberry Pi Role

The Raspberry Pi provides the PCS client network on `eth0`.

Current Pi client-side address:

```text
10.42.0.1/24
```

Current NetworkManager profile:

```text
pcs-router-wan-share
```

Despite the name, this profile is currently used as the PCS client LAN/AP handoff profile.

It provides:

- Pi address on `eth0`
- DHCP service for connected clients
- Routing/NAT through the Pi uplink
- Access to PCS local services at `10.42.0.1`

## Current Test Access Point

Current tested access point:

```text
Actiontec T3200
```

Tested settings:

```text
T3200 LAN IP:       10.42.0.2
T3200 DHCP server:  disabled
Pi eth0:            10.42.0.1
Cable:              Pi eth0 → T3200 LAN port
```

Important notes:

- Use a LAN port on the T3200.
- Do not use the DSL port.
- Do not use the T3200 WAN port for this AP/switch layout.
- The T3200 may report no internet access.
- That is okay if connected clients route through the Pi successfully.

## Expected Client Settings

A client connected to the PCS access point should receive an address from the Pi.

Example:

```text
IPv4 Address:      10.42.0.x
Subnet Mask:       255.255.255.0
Default Gateway:   10.42.0.1
```

## Windows Client Tests

From a Windows client connected to the PCS access point:

```cmd
ipconfig
ping 10.42.0.1
ping 8.8.8.8
ping google.com
```

Expected results:

- `10.42.0.1` replies from the Pi
- `8.8.8.8` confirms internet routing
- `google.com` confirms DNS

## Local Client Access

From a client behind the PCS access point:

```text
PCS Dashboard:      http://10.42.0.1
PCS Control Panel:  http://10.42.0.1:8080
Cockpit:            https://10.42.0.1:9090
Primary Share:      \\10.42.0.1\PCS-Share
Backup Share:       \\10.42.0.1\PCS-Backup
LAN NTP Server:     10.42.0.1
```

Windows NTP test:

```cmd
w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly
```

Windows file share tests:

```text
\\10.42.0.1\PCS-Share
\\10.42.0.1\PCS-Backup
```

## Connected Client Visibility

With the access point in AP/switch mode, the Pi can see clients directly on `eth0`.

Useful command:

```bash
ip neigh show dev eth0
```

Example:

```text
10.42.0.232 lladdr 10:6f:d9:d9:71:cf REACHABLE
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
10.42.0.2	Actiontec T3200 AP
10.42.0.123	Field Laptop
aa:bb:cc:dd:ee:ff	Field Laptop
```

The local file is ignored so private device names and MAC addresses are not pushed to GitHub.

## Future Cellular Layout

Future PCS field layout:

```text
Client devices
    ↓ Wi-Fi / LAN
PCS access point / switch
    ↓ LAN port
Raspberry Pi eth0 - 10.42.0.1/24
    ↓
Cellular modem
    ↓
Internet
```

The client-side network should remain mostly the same when the uplink changes from Wi-Fi to cellular.

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
ping 8.8.8.8
ping google.com
```

## Notes

The access point may claim it has no internet access because it is no longer the gateway.

The Pi is the gateway.

As long as clients receive `10.42.0.x` addresses, use `10.42.0.1` as their gateway, and pass internet/DNS tests, the PCS client network is working.
