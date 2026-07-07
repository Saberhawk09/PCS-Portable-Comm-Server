# Project Overview

PCS, or Portable Comm Server, is a portable field networking appliance built around a Raspberry Pi 4, a USB WWAN modem, and a dedicated access point/switch.

The project began after a Field Day networking failure caused by relying on Windows hotspot behavior for shared logging. The goal is to provide a purpose-built field network for amateur radio events, emergency communications exercises, portable operations, and other situations where multiple client computers need a reliable local network.

PCS is intended to provide:

- reliable wired and wireless LAN connectivity
- local file sharing for logging computers
- cellular internet access when available
- GPS-backed network time
- web-based system monitoring and control
- simple operation by non-technical users
- future rugged enclosure and field power support

## Current Project Status

PCS is no longer just a planning project. The core software baseline is working and has passed field testing.

Current focus:

- polish the software
- improve reliability and error handling
- clean up startup behavior
- bring documentation up to date
- prepare the repository for a usable release

## Current Tested Hardware

Current tested hardware includes:

- Raspberry Pi 4
- Raspberry Pi I2C RTC module
- USB WWAN enclosure
- Sierra Wireless / Semtech EM7565 LTE modem with heatsink
- Linksys EA4500 running OpenWrt
- USB flash drive for primary Samba storage
- external GNSS antenna
- LTE antennas / modem antennas as available

The Linksys EA4500 is reachable on the PCS LAN at:

```text
10.42.0.2