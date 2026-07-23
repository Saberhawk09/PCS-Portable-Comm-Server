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
- [Linksys EA4500 OpenWrt AP Setup](linksys-ea4500-ap.md)
- [Samba File Share](samba-file-share.md)
- [PCS Control Panel](pcs-control-panel.md)
- [Testing Checklist](testing-checklist.md)
- [Script Reference](../scripts/README.md)

## Hardware Docs

- [Bill of Materials](bill-of-materials.md)
- [Power System](power-system.md)
- [Wiring Notes](../hardware/wiring-notes.md)
- [Enclosure Notes](../hardware/enclosure-notes.md)

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
Dashboard:       http://10.42.0.1
Control Panel:   http://10.42.0.1:8080
Cockpit:         https://10.42.0.1:9090
Primary Share:   \\10.42.0.1\PCS-Share
Backup Share:    \\10.42.0.1\PCS-Backup
NTP Server:      10.42.0.1
OpenWrt AP:      http://10.42.0.2
Pi-Star:         http://10.42.0.3
```

## Documentation Status

Some documents may still contain older planning language from before the current working build.

Current documentation cleanup priorities:

- remove stale router-WAN wording where it no longer applies
- replace old Linksys planning notes with OpenWrt AP/switch notes
- mark cellular, GPS, Chrony, Samba, and dashboard features as implemented where appropriate
- separate current field-tested hardware from future enclosure/power-system plans
- keep install and validation steps copy/paste friendly

## Repo Areas

```text
docs/                  Project documentation
hardware/              Wiring, enclosure, and physical build notes
scripts/               Setup, maintenance, status, and test scripts
systemd/               PCS service files
web/pcs-control-panel/ PCS Control Panel web interface
config/                Configuration templates and support files
```
