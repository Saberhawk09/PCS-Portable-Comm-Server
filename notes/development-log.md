# Development Log

This file tracks major project decisions, build progress, and testing notes for PCS.

> **Historical record:** Entries describe the system at the date shown and are not current setup instructions. See the [notes index](README.md) and [current project overview](../docs/project-overview.md) before using any command, address, hardware status, or topology from this log.

## 2026-07-01

- Created public GitHub repository
- Added initial documentation structure
- Added project overview document
- Added bill of materials
- Added power system document
- Added network design document
- Added Raspberry Pi setup plan
- Added testing checklist
- Added wiring and enclosure notes
- Added documentation links to the main README
- Created first GitHub issues
- Created Prototype v0.1 milestone

## 2026-07-01 Status Snapshot

The GitHub repository has been created and initial documentation is being organized.

The PCS design goal is still a portable field networking/server system with routing, cellular internet, LAN file sharing, GPS-disciplined NTP, and grid/emergency power support.

The following hardware is currently on hand:

- Raspberry Pi 4 8GB
- Router
- Sierra Wireless EM7565
- WWAN M.2 to USB adapter
- MHF4 to SMA pigtails
- Thermal pads
- GPS antenna

The following major items are still being finalized or purchased:

- Power input hardware
- Fuse holders and fuses
- 12V to 5V buck converter
- AC to 12V internal power supply
- LTE antennas
- External storage
- Enclosure materials
- Switches, labels, and final mounting hardware

Software setup and configuration can begin once a dedicated Raspberry Pi SD card is available.

## 2026-07-01 Planned Follow-Up

- Router hardware decision
- Power system verification
- Raspberry Pi base image setup
- WWAN modem detection
- GPS/GNSS detection
- First successful file share test
- First successful client network test
- First successful cellular WAN test

## 2026-07-01 - Pre-WWAN Software Baseline Complete

Completed the pre-WWAN Raspberry Pi software bring-up phase.

Confirmed working:

- Raspberry Pi OS Desktop 64-bit baseline
- Raspberry Pi Connect remote shell and screen sharing
- I2C RTC
- Chrony/NTP with RTC sync and local fallback
- LAN NTP service on `10.42.0.1`
- Router WAN handoff over Ethernet using `pcs-router-wan-share`
- Windows client internet access through Pi and test router
- Samba test share at `\\10.42.0.1\PCS-Share`
- Cockpit access at `https://10.42.0.1:9090`
- Cockpit/systemd PCS service restart button
- PCS status script
- PCS Pi-side self-test script
- Central PCS base setup script

Current blockers:

- WWAN adapter / EM7565 cellular testing
- GPS/GNSS source configuration
- Final removable-storage Samba share
- Power hardware and enclosure build

## 2026-07-01 - Fresh Install Rebuild Test Passed

Performed a full reinstall/rebuild test of the PCS Raspberry Pi software baseline.

Confirmed:

- Fresh OS install could be configured from the GitHub repository
- `setup-pcs-base.sh` completed successfully
- Pi-side self-test passed after reinstall
- Router WAN handoff worked after reinstall
- Windows client behind test router had internet access
- Windows client could ping the Pi at `10.42.0.1`
- Windows client could use PCS LAN NTP at `10.42.0.1`
- RTC, Samba, Chrony, Cockpit, and Raspberry Pi Connect were working

Result:

Pre-WWAN software baseline is reproducible from the repository.

## 2026-07-02 - Pre-WWAN Software Baseline Complete

Reached a clean pre-WWAN software baseline for PCS.

### Confirmed Working

- Raspberry Pi 4 acting as PCS client network gateway at `10.42.0.1`
- Actiontec T3200 configured as AP/switch-style client access point
- T3200 LAN IP set to `10.42.0.2`
- T3200 DHCP disabled
- Pi connected to T3200 LAN port
- Clients receive `10.42.0.x` addresses directly from the Pi
- Client internet routing works through Pi uplink
- Chrony LAN NTP working on `10.42.0.1`
- RTC present and configured for UTC
- Samba primary share moved to USB storage
- Samba backup share stored on Pi SD card
- USB primary share syncs to SD backup mirror
- PCS Control Panel dashboard working on port `8080`
- Port 80 dashboard redirect working at `http://10.42.0.1`
- Local client info added to dashboard
- Friendly client name map support added
- `pcs-self-test.sh` updated to check dashboard redirect
- Documentation expanded across README and docs folder

### Current Client Access

```text
PCS Dashboard:      http://10.42.0.1
PCS Control Panel:  http://10.42.0.1:8080
Cockpit:            https://10.42.0.1:9090
Primary Share:      \\10.42.0.1\PCS-Share
Backup Share:       \\10.42.0.1\PCS-Backup
LAN NTP Server:     10.42.0.1
```

### Latest Clean Self-Test

```text
Pass: 48
Warn: 0
Fail: 0
Skip: 0

PCS Pi-side self-test PASSED.
```

### Status at the End of This Snapshot

PCS is now in a stable WWAN/GPS baseline state.

The remaining hardware-dependent items are:

- Future EM7565 modem validation
- Final LTE/GPS antenna mounting
- Final enclosure and power integration
- Final power system
- Final enclosure

## 2026-07-02 - WWAN/GPS Baseline Complete

Validated the Sierra Wireless WWAN modem through the M.2 USB adapter.

Working baseline:

- ModemManager detects the WWAN modem
- NetworkManager has a manual T-Mobile cellular profile
- Cellular data can be connected, disconnected, and tested from the PCS Control Panel
- WWAN modem GPS NMEA is available on /dev/ttyUSB1 at 115200 baud
- pcs-wwan-gps-nmea.service starts the GPS NMEA stream
- gpsd reads /dev/ttyUSB1
- Chrony sees the GPS source through SHM refclock 0
- PCS dashboard shows the WWAN modem NMEA to gpsd to Chrony GPS path
- Pi-side self-test passes with no warnings or failures

Final tested GPS path:

WWAN modem GPS -> /dev/ttyUSB1 NMEA -> gpsd -> Chrony -> PCS LAN NTP

This replaces the earlier temporary ModemManager location bridge approach.

Remaining work:

- Full SD-card wipe/rebuild repeatability test
- Future EM7565 validation
- Final LTE/GPS antenna mounting
- Final enclosure and power integration

## Superseded Information

The dated entries above intentionally retain early project state. The following items are no longer current:

- The Actiontec T3200 and Windstream router paths were temporary bring-up hardware. The current access point/switch is the Linksys EA4500 running OpenWrt at `10.42.0.2`.
- The Raspberry Pi is now the PCS gateway and owns DHCP, DNS, NAT/routing, and services at `10.42.0.1`; the EA4500 is a bridged AP/switch.
- The temporary Pi Wi-Fi uplink and router-WAN test path were replaced by the current optional, manually controlled EM7565 cellular uplink.
- The temporary `/srv/pcs-share` primary share was replaced by USB-backed `PCS-Share` with the SD-card `PCS-Backup` mirror.
- Port 80 now hosts the unified public homepage and authenticated administration. Port 8080 is only a compatibility redirect to `/admin/`.
- WWAN GNSS through `/dev/ttyUSB1`, gpsd, and Chrony is implemented; the earlier “future GPS” notes are complete.
- Historical self-test counts are evidence from those snapshots, not the current expected count.

For the maintained system state, use the [Project Overview](../docs/project-overview.md), [Network Topology](../docs/network-topology.md), and [Testing Checklist](../docs/testing-checklist.md).
