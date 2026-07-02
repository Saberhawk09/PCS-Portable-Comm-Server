# PCS Network Topology

PCS is currently built around the Raspberry Pi acting as the main gateway for the client network.

The Wi-Fi/router device is used as an access point or switch, while the Pi handles the client-side gateway function.

## Current Tested Topology

Current pre-WWAN test layout:

```text
Client devices
    ↓ Wi-Fi / LAN
Actiontec T3200 in AP/switch-style mode
    ↓ LAN port
Raspberry Pi eth0 - 10.42.0.1/24
    ↓
Raspberry Pi uplink - wlan0
    ↓
Home router / internet

##

Client devices
    ↓ Wi-Fi / LAN
PCS access point / router in AP mode
    ↓ LAN port
Raspberry Pi eth0 - 10.42.0.1/24
    ↓
Cellular modem
    ↓
Internet
