# Project Overview

PCS, or Portable Comm Server, is a portable field networking appliance built around a Raspberry Pi 4, a USB WWAN modem, and a dedicated access point/switch.

The project began after a networking failure caused by relying on Windows hotspot behavior for shared files. The goal is to provide a purpose-built field network for emergency communications exercises, portable operations, and other situations where multiple client computers need a reliable local network.

PCS is intended to feature/provide:

- Reliable wired and wireless LAN connectivity
- LAN file sharing for connected clients
- Cellular internet access when available
- GPS/Internet disciplined NTP server
- Web-based system monitoring and control
- Simple operation by non-technical users
- Rugged enclosure and field power support

## Current Project Status

PCS is no longer just a planning project. The core software baseline is working and rebuild-tested.

Current focus:

- Polish the software
- Improve reliability and error handling with setup and operation
- Prepare the repository for a usable release

## Current Tested Hardware

Current tested hardware includes:

- Raspberry Pi 4
- DS1307 I2C RTC module
- USB WWAN enclosure
- Sierra Wireless EM7455 and EM7565 LTE modems
- Linksys EA4500 running OpenWrt
- USB flash drive for primary Samba storage

## Current Network Layout

PCS uses the Raspberry Pi as the network brain.

The Linksys EA4500 running OpenWrt acts as a bridged access point and Ethernet switch. It does not provide DHCP, DNS, NAT, or primary routing in the current PCS design.

```text
Field clients
Windows laptops / phones / tablets
        |
        | Wi-Fi / Ethernet
        v
Linksys EA4500 running OpenWrt
Bridge / AP / switch
10.42.0.2
        |
        | Ethernet LAN
        v
Raspberry Pi 4
PCS server / gateway
10.42.0.1
        |
        +-- DHCP / DNS
        +-- Samba file sharing
        +-- Chrony LAN NTP
        +-- RTC support
        +-- GPSD
        +-- PCS Control Panel
        +-- dashboard redirect
        +-- optional cellular internet
                |
                v
        EM7565 USB WWAN modem
```

## Current Working Software Features

The current software baseline includes:

- PCS Control Panel
- Dashboard redirect from `http://10.42.0.1`
- Pi-side status and self-test scripts
- Ethernet client handoff on the PCS LAN
- OpenWrt AP/switch integration
- Samba primary share
- Samba backup share
- USB primary storage support
- SD-card backup mirror
- Manual backup sync
- Chrony LAN NTP server
- Raspberry Pi RTC support
- Sierra Wireless WWAN modem support
- WWAN GPS NMEA path
- EM7565 w/active GPS antenna
- gpsd support
- Chrony GPS source support
- Cockpit access
- systemd service integration

## Current Client Access

From a client connected to the PCS network:

```text
PCS Dashboard:      http://10.42.0.1
PCS Control Panel:  http://10.42.0.1:8080
Cockpit:            https://10.42.0.1:9090
Primary Share:      \\10.42.0.1\PCS-Share
Backup Share:       \\10.42.0.1\PCS-Backup
LAN NTP Server:     10.42.0.1
OpenWrt AP:         http://10.42.0.2
```

## Cellular Internet Philosophy

Cellular internet is intentionally manual and optional.

PCS should remain useful even when cellular service is unavailable. The LAN, Samba shares, dashboard, RTC, and local NTP service should continue working without internet access.

The cellular modem may be detected and configured, but the cellular data connection is expected to be manually controlled from the PCS Control Panel.

This avoids unwanted reconnect behavior, surprise data usage, and confusing automatic state changes in the field.

## GPS / NTP Philosophy

PCS is designed to provide stable local network time.

The intended time hierarchy is:

```text
GNSS NMEA data from WWAN modem
        |
        v
gpsd
        |
        v
Chrony
        |
        v
PCS LAN clients
```

The DS1307 RTC provides a sane time source at boot before GPS or internet time is available.

Chrony provides NTP service to LAN clients at:

```text
10.42.0.1
```

## Storage Philosophy

PCS uses removable USB storage as the primary working share and an SD-card mirror as backup.

```text
\\10.42.0.1\PCS-Share   -> /mnt/pcs-usb/PCS-Share
\\10.42.0.1\PCS-Backup  -> /srv/pcs-share-backup
```

`PCS-Share` is the main working field share.

`PCS-Backup` is the local backup mirror.

The backup mirror is intended to reduce the risk of losing field logs if the removable USB storage is lost, damaged, or accidentally removed.

## Design Goals

PCS should be:

- Reliable after reboot
- Easy to test
- Easy to rebuild
- Understandable from documentation
- Usable without internet
- Usable by non-technical operators
- Field-serviceable
- Modular enough to improve over time

## Still Planned

The current build works, but the following items are still planned or not final:

- Final rugged enclosure
- Final power system implementation using the documented 24 V -> regulated 12 V -> regulated 5 V architecture
- External LTE and GNSS antenna mounting
- Battery voltage monitoring hardware
- Low-voltage alarm / safe shutdown hardware
- OLED display
- Full SD-card wipe/rebuild repeatability validation
- Polished public release process

## Related Documentation

Start with:

- [Network Topology](network-topology.md)
- [Network Design](network-design.md)
- [Linksys EA4500 AP Setup](linksys-ea4500-ap.md)
- [Raspberry Pi Setup](raspberry-pi-setup.md)
- [Samba File Share](samba-file-share.md)
- [PCS Control Panel](pcs-control-panel.md)
- [Testing Checklist](testing-checklist.md)
- [Bill of Materials](bill-of-materials.md)
- [Power System](power-system.md)
