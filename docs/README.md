# PCS Documentation

This folder contains the main technical documentation for PCS.

The root `README.md` is the public front page. These documents hold the deeper build notes, architecture details, setup references, and validation procedures.

## Core Docs

- [Project Overview](project-overview.md)
- [Network Topology](network-topology.md)
- [Network Design](network-design.md)
- [Raspberry Pi Setup](raspberry-pi-setup.md)
- [Full-Stack Reinstall Runbook](full-stack-reinstall.md)
- [EM7565 GPS / GNSS Notes](em7565-gps-notes.md)
- [GPS Network Sharing](gps-network-sharing.md)
- [Pi-Star Integration](pi-star-integration.md)
- [Dire Wolf / APRS Integration](direwolf-aprs.md)
- [Meshtastic USB/Bluetooth MQTT Gateway](meshtastic-bluetooth-gateway.md)
- [PCS GPIO Allocation](gpio-allocation.md)
- [Linksys EA4500 OpenWrt AP Setup](linksys-ea4500-ap.md)
- [Samba File Share](samba-file-share.md)
- [PCS Control Panel](pcs-control-panel.md)
- [Cockpit Service Button](cockpit-service-button.md)
- [Testing Checklist](testing-checklist.md)
- [Release Checklist](release-checklist.md)
- [Script Reference](../scripts/README.md)

## Hardware Docs

- [Bill of Materials](bill-of-materials.md)
- [Power System](power-system.md)
- [Wiring Notes](../hardware/wiring-notes.md)
- [Enclosure Notes](../hardware/enclosure-notes.md)

## Historical Comparison Notes

- [Cellular Plan and Modem Comparison Notes](cellular-plan-modem-testing.md) - dated operator testing context, not a current plan recommendation

## Current Working Architecture

```text
Raspberry Pi 4:         10.42.0.1
Linksys EA4500 OpenWrt: 10.42.0.2
Pi-Star hotspot:        10.42.0.3
PCS LAN subnet:         10.42.0.0/24
```

The Raspberry Pi is the PCS server, gateway, DHCP/DNS provider, Samba host, NTP server, GPSD/Chrony host, dashboard host, and control panel host.

The Linksys EA4500 runs OpenWrt and acts as a bridged access point and Ethernet switch.

## Current Client Access

From a client connected to the PCS network:

```text
PCS Homepage:    http://10.42.0.1/
Admin Login:     http://10.42.0.1/admin/
Cockpit:         https://10.42.0.1:9090
Primary Share:   \\10.42.0.1\PCS-Share
Backup Share:    \\10.42.0.1\PCS-Backup
NTP Server:      10.42.0.1
OpenWrt AP:      http://10.42.0.2
Pi-Star:         http://10.42.0.3 (when selected during setup)
```

## Documentation Status

The software, network, storage, WWAN/GNSS, Pi-Star, managed Dire Wolf/APRS, and
Meshtastic documents describe the commissioned system as of August 24, 2026.
The Pi-side wipe/rebuild path was most recently verified on August 18, 2026;
the synchronized current stack passed 133 live self-tests without warnings or
failures on August 24.

The current documentation uses these status boundaries:

- **Installed / tested:** working in the current PCS build
- **Software staged:** installed and testable without activating dependent hardware or RF
- **Purchased / awaiting delivery:** acquired but not installed or validated
- **As-built record pending:** operational hardware whose exact wiring, measurements, mounting, or part reconciliation is still incomplete

The SA818S/Easy Digi APRS hardware, UART, GPIO6 PTT, Sabrent/C-Media audio,
144.5500 MHz RF path, two-way APRS-IS messaging, GNSS beaconing, and WIDE1-1
fill-in operation are installed and tested. The persistent RAK4631 USB/MQTT
gateway, GPSD position delivery, and privacy-safe local temperature/humidity
telemetry are deployed and live-validated. IJC1 public-map visibility and IJC2
RF-to-map forwarding are demonstrated; broader RF coverage and sensor accuracy
remain operator checkpoints.

Current documentation priorities:

- capture exact enclosure dimensions, mounting locations, and CAD/export references
- reconcile power and wiring notes with the physical build
- record measured rail voltage, current draw, fuse sizes, wire gauge, and thermal results
- characterize Meshtastic range beyond the commissioned RF-to-map test and establish a referenced sensor baseline
- preserve the deployed RAK4631 USB transport, NeoMesh proxy, 30-minute GPSD position cadence, hourly map reporting, and 15-bit public position policy
- extend the general testing and release checklists for the guarded APRS workflow
- keep install and validation steps copy/paste friendly
- update the changelog and release checklist with every public release

## Repo Areas

```text
docs/                  Project documentation
hardware/              Wiring, enclosure, and physical build notes
scripts/               Setup, maintenance, status, and test scripts
systemd/               PCS service files
web/pcs-control-panel/ PCS homepage and administrative web interface
config/                Configuration templates and support files
```
