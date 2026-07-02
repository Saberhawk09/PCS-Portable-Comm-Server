# PCS Testing Checklist

This checklist is used after setup, reboot, or major configuration changes.

## Pi-Side Validation

Run from the repository root:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
git status
./scripts/pcs-self-test.sh
```

Expected Git result:

```text
nothing to commit, working tree clean
```

Expected self-test result:

```text
PCS Pi-side self-test PASSED.
```

A healthy system should show:

```text
Fail: 0
Warn: 0
```

## PCS Status

Run:

```bash
./scripts/pcs-status.sh
```

Confirm these are present:

```text
Wi-Fi uplink wlan0:       connected
Ethernet handoff eth0:    connected
Router-side IP present:   yes (10.42.0.1/24)
RTC:                      present
Chrony/NTP:               active
GPSD:                     active if EM7455 GPS is configured
EM7455 GPS:               NMEA present if WWAN GPS is configured
Samba:                    active
Primary share:            present (PCS-Share)
Backup share:             present (PCS-Backup)
PCS Control Panel:        active
Dashboard Redirect:       active
```

## Client Network Test

From a Windows client connected to the PCS access point:

```cmd
ipconfig
ping 10.42.0.1
ping 8.8.8.8
ping google.com
```

Expected client network settings:

```text
IPv4 Address:      10.42.0.x
Subnet Mask:       255.255.255.0
Default Gateway:   10.42.0.1
```

Expected ping results:

- `10.42.0.1` replies from the Pi
- `8.8.8.8` confirms internet routing
- `google.com` confirms DNS

## Web Dashboard Test

From a PCS client, open:

```text
http://10.42.0.1
```

Expected behavior:

```text
http://10.42.0.1 redirects to http://10.42.0.1:8080
```

The dashboard should show status cards and local client info.

Direct control panel URL:

```text
http://10.42.0.1:8080
```

## Cockpit Test

From a PCS client, open:

```text
https://10.42.0.1:9090
```

Cockpit should load.

## Samba Share Test

From Windows File Explorer:

```text
\\10.42.0.1\PCS-Share
\\10.42.0.1\PCS-Backup
```

Expected:

- `PCS-Share` opens the USB primary share
- `PCS-Backup` opens the SD-card backup mirror

## NTP Test

From Windows:

```cmd
w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly
```

Expected:

- Samples return successfully
- Offset remains reasonably stable

## Backup Sync Test

From the Pi or PCS Control Panel, run the backup sync.

Terminal command:

```bash
./scripts/sync-pcs-share-to-backup.sh
```

Expected:

```text
/mnt/pcs-usb/PCS-Share → /srv/pcs-share-backup
```

Confirm `LAST_SYNC.txt` updates:

```bash
cat /srv/pcs-share-backup/LAST_SYNC.txt
```

## USB Mount Test

Check USB storage:

```bash
findmnt /mnt/pcs-usb
mount | grep pcs-usb
```

Expected:

```text
/dev/sdX1 mounted on /mnt/pcs-usb
```

There should only be one mount entry for `/mnt/pcs-usb`.

## Connected Client Visibility

On the Pi:

```bash
ip neigh show dev eth0
```

Expected with AP/switch mode:

```text
10.42.0.x lladdr xx:xx:xx:xx:xx:xx REACHABLE
```

Old `FAILED` entries are usually stale neighbor entries.

They can be cleared with:

```bash
sudo ip neigh flush dev eth0
```

## Reboot Test

Run:

```bash
sudo reboot
```

After reboot:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/pcs-self-test.sh
```

Expected:

```text
PCS Pi-side self-test PASSED.
```

## WWAN / GPS Expectations

PCS now supports the tested Dell DW5811e / Sierra Wireless EM7455 GPS path:

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

When the EM7455/DW5811e modem and GPS antenna are installed and configured, these are expected:

```text
ModemManager detects modem
cdc-wdm0 exists
wwan0 exists
/dev/ttyUSB1 exists
pcs-em7455-gps-nmea.service enabled
pcs-em7455-gps-nmea.service active/exited
gpsd active
gpsd receiving NMEA
Chrony GPS source present
Chrony GPS source reach nonzero
```

Useful checks:

```bash
mmcli -L
nmcli device status
systemctl status pcs-em7455-gps-nmea.service --no-pager -l
systemctl status gpsd.service --no-pager -l
chronyc sources -v
./scripts/pcs-status.sh
./scripts/pcs-self-test.sh
```

If the WWAN/GPS hardware is not installed, modem/GPS-related warnings may be expected. With the EM7455/DW5811e GPS setup installed and working, `gpsd inactive` is no longer expected.
