# Raspberry Pi Setup

This document tracks the planned Raspberry Pi software setup for PCS.

The Raspberry Pi is responsible for server-side services, while the router handles client networking.

## Target System

Planned starting point:

- Raspberry Pi 4 8GB
- Raspberry Pi OS with desktop, 64-bit
- SSH enabled
- Raspberry Pi Connect enabled
- I2C RTC enabled
- System fully updated before service configuration

## Planned Pi Roles

- Cellular WAN connection through the WWAN modem
- LAN file share from removable storage
- GPS receiver access through the modem GNSS function
- GPS-disciplined time using GPSD and Chrony
- Ethernet connection to the router WAN port

## Initial Setup Checklist

- [ ] Flash Raspberry Pi OS with desktop, 64-bit, to SD card
- [ ] Enable SSH
- [ ] Set hostname
- [ ] Set username and password
- [ ] Boot Pi
- [ ] Confirm Raspberry Pi Connect remote shell
- [ ] Confirm Raspberry Pi Connect screen sharing
- [ ] Update system packages
- [ ] Confirm Ethernet connection
- [ ] Confirm Wi-Fi connection, if used during setup
- [ ] Confirm I2C RTC is detected
- [ ] Confirm USB WWAN modem is detected
- [ ] Confirm external storage is detected
- [ ] Confirm GPS / GNSS function is visible

## Dependency Installation

After installing Raspberry Pi OS with desktop, 64-bit, clone this repository and run the dependency installer.

Commands:

    git clone https://github.com/Saberhawk09/PCS-Portable-Comm-Server.git
    cd PCS-Portable-Comm-Server
    ./scripts/install-dependencies.sh

The installer adds baseline PCS packages for:

- General system utilities
- Hardware inspection tools
- NetworkManager
- ModemManager
- QMI / MBIM modem tools
- Samba / SMB
- GPSD
- Chrony
- Cockpit
- WireGuard tools

The installer does not configure routing, Samba shares, GPSD, Chrony GPS sources, or modem profiles.

## Status Script

After dependencies are installed, the PCS status script can be used to collect useful system information.

Command:

    ./scripts/pcs-status.sh

The status script reports:

- Hostname
- OS version
- Kernel version
- Uptime
- Time / NTP / RTC status
- NetworkManager devices
- IP addresses
- Routes
- DNS configuration
- USB devices
- I2C bus status
- ModemManager modem list
- Key service states
- Raspberry Pi Connect status

## Planned Services

| Service | Purpose | Status |
|---|---|---|
| NetworkManager | Network and connection management | Installed |
| ModemManager | WWAN modem control | Installed |
| Samba / SMB | LAN file share | Installed |
| GPSD | GPS data service | Installed |
| Chrony | NTP time discipline | Installed |
| Cockpit | Web management UI | Installed |
| Pi-hole / AdGuard | DNS filtering | Future |
| WireGuard | VPN access | Future |

## Cellular WAN

The WWAN modem will provide internet access.

Planned hardware:

- Sierra Wireless EM7565
- M.2 WWAN to USB adapter
- External LTE antennas
- MHF4 to SMA antenna pigtails

The Pi may provide WAN output to the router over Ethernet.

Exact modem configuration will be added once the WWAN adapter is available for testing.

## LAN File Share

The Pi will host a LAN-accessible file share from removable storage.

Possible uses:

- Shared logging files
- Configuration backups
- Field documents
- Radio software files
- Offline reference material

A temporary local test share may be used before final removable storage is installed.

## GPS / NTP

The modem GNSS receiver will be used as the GPS source.

Planned software stack:

- GPSD for GPS data
- Chrony for NTP discipline

The goal is to provide reliable local network time even when normal internet NTP is unavailable.

## RTC

The Pi currently has an I2C RTC attached.

Confirmed working:

- RTC detected as /dev/rtc0
- DS1307-compatible driver loaded
- System clock can be set from RTC
- RTC is kept in UTC, not local time

The working RTC config is backed up on the Pi as:

    /boot/firmware/config.txt.pcs-rtc-working

## Current Tested Status

Confirmed working on the PCS test Pi:

- Raspberry Pi Connect remote shell
- Raspberry Pi Connect screen sharing
- Local SSH
- I2C RTC
- NTP synchronization
- Chrony active
- NetworkManager active
- ModemManager active
- Avahi active as pcs-pi.local
- Cockpit web UI reachable from Windows on port 9090
- PCS dependency installer tested successfully
- PCS status script tested successfully

## Notes

Avoid changing display or HDMI settings unless Raspberry Pi Connect screen sharing breaks again.

Exact routing, modem, Samba, GPSD, and Chrony configuration commands will be added as each subsystem is tested.
