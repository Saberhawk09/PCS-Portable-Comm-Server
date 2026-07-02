# PCS Project Overview

PCS stands for Portable Communication Server.

It is a field-deployable networking appliance built around a Raspberry Pi 4.

The goal is to provide local networking, file sharing, time service, monitoring, and eventually cellular internet/GPS-backed time in one portable box.

## Purpose

PCS is designed for portable operations where multiple client devices need a reliable local network.

Example use cases:

- Amateur radio Field Day logging
- Emergency communications exercises
- Portable network demos
- Temporary LAN file sharing
- Field operations where normal internet/networking is unreliable

## Current Working Baseline

The current pre-WWAN baseline provides:

- Raspberry Pi gateway at `10.42.0.1`
- Client LAN/AP handoff through `eth0`
- Internet sharing through the Pi uplink
- USB primary Samba file share
- SD-card Samba backup mirror
- Chrony LAN NTP server
- RTC support
- Cockpit web UI
- PCS Control Panel dashboard
- Port 80 redirect to dashboard
- Pi-side self-test and status scripts

## Current Test Layout

```text
Client devices
    ↓ Wi-Fi / LAN
Access point / switch
    ↓ LAN port
Raspberry Pi eth0 - 10.42.0.1/24
    ↓
Raspberry Pi uplink - wlan0 now, cellular later
    ↓
Internet
```

## Current Client Access

```text
PCS Dashboard:      http://10.42.0.1
PCS Control Panel:  http://10.42.0.1:8080
Cockpit:            https://10.42.0.1:9090
Primary Share:      \\10.42.0.1\PCS-Share
Backup Share:       \\10.42.0.1\PCS-Backup
LAN NTP Server:     10.42.0.1
```

## Planned Final Features

Planned future features include:

- Cellular internet uplink
- GPS/GNSS receiver
- GPS-disciplined Chrony/NTP
- Final enclosure
- Final power switching and fusing
- External antennas
- LTE signal/status display
- Possible battery/voltage monitoring

## Design Philosophy

PCS should be:

- Reliable
- Portable
- Easy to operate
- Rebuildable from scripts
- Well documented
- Useful without internet
- Capable of using cellular internet when available

## Current Status

The project is currently in the pre-WWAN software baseline stage.

The main software stack is working and documented. Cellular modem and GPS/GNSS setup are waiting on hardware.
