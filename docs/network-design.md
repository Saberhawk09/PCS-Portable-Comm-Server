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