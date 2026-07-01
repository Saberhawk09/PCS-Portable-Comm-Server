# Development Log

This file tracks major project decisions, build progress, and testing notes for PCS.

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

## Current Status

The GitHub repository has been created and initial documentation is being organized.

The PCS design goal is still a portable field networking/server system with routing, cellular internet, LAN file sharing, GPS-disciplined NTP, and grid/emergency power support. :contentReference[oaicite:0]{index=0}

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

## Next Notes To Capture

- Router hardware decision
- Power system verification
- Raspberry Pi base image setup
- WWAN modem detection
- GPS/GNSS detection
- First successful file share test
- First successful client network test
- First successful cellular WAN test