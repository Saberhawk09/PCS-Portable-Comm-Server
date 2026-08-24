# Project Overview

PCS, or Portable Comm Server, is a portable field networking appliance built around a Raspberry Pi 4, a USB WWAN modem, and a dedicated access point/switch.

The project began after a networking failure caused by relying on Windows hotspot behavior for shared files. The goal is to provide a purpose-built field network for emergency communications exercises, portable operations, and other situations where multiple client computers need a reliable local network.

PCS is intended to feature/provide:

- Reliable wired and wireless LAN connectivity
- LAN file sharing for connected clients
- Cellular internet access when available
- GPS/Internet disciplined NTP server
- Web-based system monitoring and control
- Optional Pi-Star, APRS, and Meshtastic USB/Bluetooth MQTT integration
- Simple operation by non-technical users
- Rugged enclosure and field power support

## Current Project Status

PCS is an operational v1 hardware and software prototype. The core Pi-side software baseline is rebuild-tested, and the assembled hardware provides the intended LAN, storage, time, GNSS, monitoring, optional cellular, and Pi-Star services.

The Dire Wolf / APRS hardware path is commissioned: SA818S UART control,
stock Easy Digi isolation, GPIO6 PTT, Sabrent/C-Media USB audio, 144.5500 MHz
RF TX/RX, GNSS beaconing, two-way APRS-IS messaging, and WIDE1-1 fill-in
digipeating have been demonstrated. Repository integration manages radio/audio
preparation, service recovery, LAN-only clients, monitoring, and rollback. The
RAK4631 is deployed over USB with its persistent MQTT session, GPSD position
delivery, and privacy-safe local telemetry validated on the live PCS. IJC1 has
appeared on the public map, and an opted-in IJC2 position heard over LoRa was
forwarded through PCS and appeared on that map, demonstrating the remote
RF-to-map gateway path. Broader RF coverage and the local environment sensor's
accuracy remain operator checkpoints. The exact as-built power, wiring,
grounding, thermal, enclosure, and mounting records are also still pending.

Current focus:

- Polish the software
- Improve reliability and error handling with setup and operation
- Characterize Meshtastic range beyond the commissioned RF-to-map test and compare its case sensor with a reference
- Keep documentation and releases aligned with the fielded system

## Current Tested Hardware

Current tested hardware includes:

- Raspberry Pi 4
- DS1307 I2C RTC module
- Sierra Wireless EM7565 LTE modem and USB WWAN adapter
- Linksys EA4500 running OpenWrt
- USB flash drive for primary Samba storage
- External LTE and active GNSS antennas
- Pi-Star hotspot at `10.42.0.3`
- Operational AC/DC source-selector power system
- Two 120 mm cooling fans
- HD44780 16x2 LCD status display
- MAX7219 8x8 health-annunciator matrix
- Six-pixel WS2812 status-indicator chain
- Armor Lite cooler with GPIO18 hardware-PWM fan control
- SA818S, stock Easy Digi, Sabrent/C-Media USB audio, and GPIO6 APRS PTT path
- RAK4631 USB Meshtastic gateway with demonstrated NeoMesh/public-map forwarding

Installed with as-built records or measurements pending:

- RAK4631 sensor model/mounting record and referenced environment baseline

Purchased hardware is not treated as installed or validated until it has been commissioned and tested.

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
        +-- PCS public homepage
        +-- authenticated admin control panel
        +-- optional Pi-Star integration at 10.42.0.3
        +-- managed Dire Wolf / SA818S APRS subsystem
        +-- optional cellular internet
                |
                v
        EM7565 USB WWAN modem
```

## Current Working Software Features

The current software baseline includes:

- Public PCS status homepage
- Password-protected administration at `/admin/`
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
- LAN-only GPSD sharing for trusted PCS clients
- Optional Pi-Star monitoring, GPS/time integration, and installer-assisted coordinated-shutdown pairing
- Managed Dire Wolf 1.8.1 with SA818S boot programming, ALSA restoration, LAN-only AGW/KISS, telemetry, and guarded activation/rollback
- Cockpit access
- systemd service integration

Meshtastic integration is deployed as a repeatable, persistent RAK4631
USB-to-MQTT client-proxy gateway. The USB session, broker connection, bounded
GPSD position delivery, encrypted NeoMesh proxy publishes, opted-in hourly map
reporting, 30-minute PCS position packets, and privacy-safe runtime telemetry
are live-validated. The public/admin
dashboards report approved aggregate node, MQTT, mesh, GPSD, environment,
utilization, power, broker-policy, and map-policy fields. Public-map appearance
for IJC1 and remote RF-to-map forwarding for IJC2 have been demonstrated.
Broader RF coverage and case-sensor accuracy still require operator observation
and comparison with a reference.

## Current Client Access

From a client connected to the PCS network:

```text
PCS Homepage:       http://10.42.0.1/
PCS Admin Login:    http://10.42.0.1/admin/
Cockpit:            https://10.42.0.1:9090
Primary Share:      \\10.42.0.1\PCS-Share
Backup Share:       \\10.42.0.1\PCS-Backup
LAN NTP Server:     10.42.0.1
OpenWrt AP:         http://10.42.0.2
Pi-Star Dashboard:  http://10.42.0.3 (when selected during setup)
```

## Cellular Internet Philosophy

Cellular internet is optional. Fresh installs default to manual control and can
instead enable automatic Wi-Fi-to-cellular fallback.

PCS should remain useful even when cellular service is unavailable. The LAN, Samba shares, dashboard, RTC, and local NTP service should continue working without internet access.

The cellular modem may be detected and configured while data remains
disconnected. In manual mode the PCS Control Panel owns connection changes. In
`wifi-fallback` mode, PCS connects after sustained Wi-Fi loss and disconnects
only the session it automatically started when Wi-Fi returns.

This avoids unwanted reconnect behavior, surprise data usage, and confusing automatic state changes in the field.

## GPS / NTP Philosophy

PCS is designed to provide stable local network time.

The installed time hierarchy is:

```text
GNSS NMEA data from WWAN modem
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

## Current Finish Work and Optional Enhancements

The working build is operational. Remaining work is primarily documentation, measurement, and optional expansion:

- capture final enclosure dimensions, mounting details, photos, and CAD references
- reconcile the documented power architecture with the physical as-built wiring
- record actual fuse values, wire gauge, rail voltage, current draw, and thermal results
- finish permanent external LTE and GNSS antenna labeling and mounting documentation
- preserve operator-supervised RF activation after any APRS hardware or profile change
- characterize Meshtastic range beyond the commissioned RF-to-map test and establish a referenced sensor baseline
- consider battery voltage monitoring and low-voltage safe shutdown hardware
- repeat the full three-device reinstall and on-air validation when OpenWrt or Pi-Star configuration changes materially

The PCS Pi SD-card wipe/rebuild was most recently verified on August 18, 2026.
The deployed stack was synchronized to current `main` and passed 133 live
self-tests with no warnings or failures on August 24, 2026. Credential entry,
external-appliance recovery, radio identity, firmware flashing, and on-air RF
checks intentionally remain manual.

## Related Documentation

Start with:

- [Network Topology](network-topology.md)
- [Network Design](network-design.md)
- [Linksys EA4500 AP Setup](linksys-ea4500-ap.md)
- [Raspberry Pi Setup](raspberry-pi-setup.md)
- [Full-Stack Reinstall Runbook](full-stack-reinstall.md)
- [Pi-Star Integration](pi-star-integration.md)
- [Dire Wolf / APRS Integration](direwolf-aprs.md)
- [Samba File Share](samba-file-share.md)
- [PCS Control Panel](pcs-control-panel.md)
- [Testing Checklist](testing-checklist.md)
- [Bill of Materials](bill-of-materials.md)
- [Power System](power-system.md)
