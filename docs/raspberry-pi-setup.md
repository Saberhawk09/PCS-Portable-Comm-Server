# Raspberry Pi Setup

This document covers the Raspberry Pi software baseline for PCS.

The current tested system is a Raspberry Pi 4 running Debian/Raspberry Pi OS.

## Repository Location

Current working repository path:

```text
/home/pi/Projects/PCS-Portable-Comm-Server
```

From the Pi:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
```

## Clone Location

This repository should be cloned under `~/Projects` so scripts and service files match the expected path.

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/Saberhawk09/PCS-Portable-Comm-Server.git
cd PCS-Portable-Comm-Server
```

Expected final path:

```text
/home/pi/Projects/PCS-Portable-Comm-Server
## Main Setup Script

Run the PCS base setup script:

```bash
./scripts/setup-pcs-base.sh
```

This configures the current PCS baseline:

- Dependencies
- RTC support
- Client LAN/AP handoff on `eth0`
- Samba shares
- Chrony LAN NTP
- Cockpit
- PCS Control Panel
- Dashboard redirect
- Self-test/status tooling

## Network Role

The Pi acts as the client network gateway.

Current client-side address:

```text
10.42.0.1/24
```

Current client-side interface:

```text
eth0
```

Current uplink interface:

```text
wlan0
```

The current NetworkManager profile is:

```text
pcs-router-wan-share
```

Despite the name, this profile currently serves the PCS client LAN/AP side.

## RTC

The Pi uses an RTC so the system clock has a useful fallback when offline.

Check RTC status:

```bash
timedatectl
ls -l /dev/rtc*
```

Expected:

```text
RTC in local TZ: no
/dev/rtc0 exists
```

## Chrony / LAN NTP

Chrony provides time service to PCS clients.

PCS LAN NTP server:

```text
10.42.0.1
```

Check Chrony:

```bash
chronyc tracking
chronyc sources -v
```

Windows test:

```cmd
w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly
```

## Samba

Current share layout:

```text
\\10.42.0.1\PCS-Share   → /mnt/pcs-usb/PCS-Share
\\10.42.0.1\PCS-Backup  → /srv/pcs-share-backup
```

Check Samba:

```bash
testparm -s
systemctl status smbd --no-pager
```

## PCS Control Panel

Dashboard URL:

```text
http://10.42.0.1
```

Direct control panel URL:

```text
http://10.42.0.1:8080
```

Check services:

```bash
systemctl status pcs-control-panel.service --no-pager -l
systemctl status pcs-dashboard-redirect.service --no-pager -l
```

## Validation

After setup or reboot:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
git status
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh
```

Expected:

```text
nothing to commit, working tree clean
PCS Pi-side self-test PASSED.
```

## Reboot Test

Run:

```bash
sudo reboot
```

After the Pi returns:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/pcs-self-test.sh
```

Expected:

```text
Fail: 0
Warn: 0
PCS Pi-side self-test PASSED.
```

## EM7455 / DW5811e GPS NMEA Setup

PCS can use the Dell DW5811e / Sierra Wireless EM7455 WWAN modem as a GPS/GNSS time and location source.

The tested GPS path is:

```text
EM7455 / DW5811e GPS
    ↓
/dev/ttyUSB1 NMEA at 115200 baud
    ↓
gpsd
    ↓
Chrony SHM refclock 0
    ↓
PCS LAN NTP server at 10.42.0.1
```

This setup does not change `AT!USBCOMP`. The tested USB composition already exposes:

```text
diag,nmea,modem,mbim
```

The GPS starter service enables GPS through ModemManager and sends `GPS_START` to `/dev/ttyUSB1`, which wakes the NMEA stream for `gpsd`.

Run this after the WWAN adapter, EM7455/DW5811e modem, and GPS antenna are installed:

```bash
./scripts/setup-em7455-gps-nmea.sh
```

Expected services after setup:

```bash
systemctl status pcs-em7455-gps-nmea.service --no-pager -l
systemctl status gpsd.service --no-pager -l
chronyc sources -v
```

Expected results:

```text
pcs-em7455-gps-nmea.service: active (exited)
gpsd.service: active (running)
gpsd device: /dev/ttyUSB1
Chrony GPS source: present with nonzero reach
```

The GPS dashboard may display live latitude/longitude and Maidenhead grid square. Do not post screenshots or raw GPS output publicly unless sharing location is acceptable.

## Cellular / WWAN Data Setup

PCS currently uses a manual NetworkManager cellular profile for T-Mobile testing:

```text
Connection name: pcs-cellular-tmobile
APN: fast.t-mobile.com
Autoconnect: disabled
Route metric: higher than Wi-Fi
```

Cellular data is intentionally manual for now. The modem can provide GPS while cellular data is disconnected.

Useful commands:

```bash
nmcli device status
mmcli -L
sudo nmcli connection up pcs-cellular-tmobile
sudo nmcli connection down pcs-cellular-tmobile
```

The control panel also includes buttons for cellular status, connect, disconnect, and cellular-only internet testing.

## External Sierra Modem Reference

PCS currently does not automatically rewrite Sierra modem firmware identity, carrier PRI, or USB composition.

For deeper Sierra EM7455/EM7565 modem setup notes, see:

https://github.com/danielewood/sierra-wireless-modems

Use external modem flashing or identity-change instructions carefully.

PCS setup scripts currently assume the modem already exposes the needed Linux interfaces:

- cdc-wdm0
- wwan0
- /dev/ttyUSB1 for NMEA GPS
- /dev/ttyUSB2 for AT commands

A future PCS modem setup script may add a read-only modem readiness check first. Automatic modem configuration should remain separate from the base PCS installer.
