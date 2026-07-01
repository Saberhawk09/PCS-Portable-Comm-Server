# Network Design

PCS is designed around a simple field network layout:

- Cellular modem provides WAN connectivity
- Raspberry Pi hosts server-side services
- Dedicated router handles client networking
- Client PCs connect over Ethernet or Wi-Fi

## Primary Network Layout

```text
LTE Antennas
      │
   WWAN Modem
      │ USB
 Raspberry Pi
 ├── SMB File Share
 ├── GPSD
 ├── Chrony / NTP
 └── Ethernet WAN Output
      │
 Dedicated Router
 ├── Wi-Fi
 ├── DHCP
 └── LAN
      │
 Client PCs

## Router WAN Handoff Test

Before the cellular modem is available, PCS can simulate the final WAN handoff path by sharing the Pi's current Wi-Fi uplink out through Ethernet.

Temporary test layout:

    Internet over Pi Wi-Fi → Pi eth0 → Router WAN → Router clients

Future PCS layout:

    Cellular modem → Pi → Pi eth0 → Router WAN → Router clients

This is handled by:

    ./scripts/setup-router-wan-share.sh

The script creates a NetworkManager shared Ethernet profile:

- Profile name: `pcs-router-wan-share`
- Interface: `eth0`
- Pi Ethernet address: `10.42.0.1/24`
- IPv4 mode: shared
- IPv6 mode: ignored

To activate after connecting the router WAN port to the Pi Ethernet port:

    sudo nmcli connection up pcs-router-wan-share

Expected result:

- The router WAN interface receives a `10.42.0.x` address from the Pi
- Router clients should be able to reach the internet through the Pi's current uplink

This test does not require the WWAN modem. It only validates the Ethernet handoff side of the design.

## Client Access To Pi Services Through Router

During router WAN handoff testing, clients behind the test router were able to access Pi-hosted services using the Pi's router-facing Ethernet address.

Confirmed working from a Windows client behind the Windstream test router:

- Ping to `10.42.0.1`
- Samba access at `\\10.42.0.1\PCS-Share`
- Cockpit access at `https://10.42.0.1:9090`

The hostname `pcs-pi.local` did not resolve from behind the router. This is expected because `.local` / mDNS normally only works within the same local broadcast domain and usually does not cross a router WAN/LAN boundary.

For field use, the reliable access addresses are currently:

- File share: `\\10.42.0.1\PCS-Share`
- Cockpit: `https://10.42.0.1:9090`

During home testing, the Pi was reachable from the test-router client network at both `10.42.0.1` and the Pi's home Wi-Fi address `192.168.50.236`.

For field use, documentation should prefer `10.42.0.1` because it is controlled by the PCS Ethernet handoff profile. The `192.168.50.236` address is only valid on the current home Wi-Fi network.
