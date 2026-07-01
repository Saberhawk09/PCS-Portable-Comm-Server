# Raspberry Pi Setup

This document will track the planned Raspberry Pi software setup for PCS.

The Raspberry Pi is responsible for server-side services, while the router handles client networking.

## Planned Pi Roles

- Cellular WAN connection through the WWAN modem
- LAN file share from removable storage
- GPS receiver access through the modem GNSS function
- GPS-disciplined time using GPSD and Chrony
- Ethernet connection to the router WAN port

## Base System Setup

Planned starting point:

- Raspberry Pi 4 8GB
- Raspberry Pi OS Lite
- Ethernet enabled
- SSH enabled
- Static or predictable network configuration
- System fully updated before service configuration

## Initial Setup Checklist

- [ ] Flash Raspberry Pi OS Lite to SD card
- [ ] Enable SSH
- [ ] Set hostname
- [ ] Update system packages
- [ ] Confirm Ethernet connection
- [ ] Confirm USB WWAN modem is detected
- [ ] Confirm external storage is detected
- [ ] Confirm GPS / GNSS function is visible

## Planned Services

| Service | Purpose | Status |
|---|---|---|
| ModemManager | WWAN modem control | Planned |
| NetworkManager | Cellular connection management | Planned |
| Samba / SMB | LAN file share | Planned |
| GPSD | GPS data service | Planned |
| Chrony | NTP time discipline | Planned |
| Cockpit | Web management UI | Future |
| Pi-hole / AdGuard | DNS filtering | Future |

## Cellular WAN

The WWAN modem will provide internet access.

Planned hardware:

- Sierra Wireless EM7565
- M.2 WWAN to USB adapter
- External LTE antennas
- MHF4 to SMA antenna pigtails

The Pi may provide WAN output to the router over Ethernet.

## LAN File Share

The Pi will host a LAN-accessible file share from removable storage.

Possible uses:

- Shared logging files
- Configuration backups
- Field documents
- Radio software files
- Offline reference material

## GPS / NTP

The modem GNSS receiver will be used as the GPS source.

Planned software stack:

- GPSD for GPS data
- Chrony for NTP discipline

The goal is to provide reliable local network time even when normal internet NTP is unavailable.

## Notes

Exact configuration commands will be added once the Pi has a dedicated SD card and the WWAN hardware is available for testing.