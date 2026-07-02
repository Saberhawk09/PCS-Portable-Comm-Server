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

## Hardware Not Configured Yet

The following are expected to be missing until hardware arrives:

```text
WWAN/cellular modem
GPS/GNSS receiver
GPSD active configuration
GPS-disciplined Chrony source
```

Before that hardware is installed, the self-test expects:

```text
No modem present yet
gpsd inactive
```
