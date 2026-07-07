# PCS Scripts

This folder contains setup, maintenance, status, and operator scripts for PCS.

Run scripts from the repository root:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
```

## Main Setup

### setup-pcs-base.sh

Runs the baseline PCS setup workflow.

```bash
./scripts/setup-pcs-base.sh
```

This installs/configures:

- Dependencies
- RTC
- Client LAN/AP handoff on `eth0`
- Samba shares
- Chrony LAN NTP
- PCS restart service
- PCS Control Panel
- Dashboard redirect
- Final status/self-test checks

## Dependencies

### install-dependencies.sh

Installs baseline packages used by PCS.

```bash
./scripts/install-dependencies.sh
```

Includes tools for networking, Samba, Chrony, GPSD, ModemManager, Cockpit, and general diagnostics.

## Time / RTC / NTP

### setup-rtc.sh

Configures the Raspberry Pi RTC overlay and I2C support.

```bash
./scripts/setup-rtc.sh
```

### setup-em7455-gps-nmea.sh

Configures the tested EM7455/DW5811e GPS path:

EM7455/DW5811e -> /dev/ttyUSB1 NMEA -> gpsd -> Chrony

Run after the WWAN USB adapter, EM7455/DW5811e modem, and GPS antenna are installed:

./scripts/setup-em7455-gps-nmea.sh

This installs/configures:

- pcs-em7455-gps-nmea.service
- gpsd on /dev/ttyUSB1
- Chrony SHM refclock 0 for GPS-backed LAN NTP

It does not change AT!USBCOMP.

### pcs-em7455-gps-nmea-start.py

Helper used by pcs-em7455-gps-nmea.service.

It enables modem GPS through ModemManager, sends GPS_START to /dev/ttyUSB1, and verifies that NMEA output is present. It hides live location in service logs.

### setup-chrony-lan-ntp.sh

Configures Chrony to serve NTP to PCS clients on:

```text
10.42.0.1
```

Windows test:

```cmd
w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly
```

## Network

### setup-router-wan-share.sh

Configures the Pi `eth0` client-side network profile.

Current role:

```text
Pi eth0: 10.42.0.1/24
```

This profile is currently used as the PCS client LAN/AP handoff.

The name still references router WAN sharing, but the current preferred topology uses the attached router/AP as a bridge/AP/switch.

## Samba Storage

### setup-test-samba-share.sh

Creates the initial `PCS-Share` Samba share.

This is still useful as a bootstrap step.

### setup-samba-backup-share.sh

Creates the SD-card backup share:

```text
\\10.42.0.1\PCS-Backup -> /srv/pcs-share-backup
```

### setup-usb-primary-share.sh

Configures removable USB storage as the primary Samba share:

```text
\\10.42.0.1\PCS-Share -> /mnt/pcs-usb/PCS-Share
```

Default USB UUID:

```text
340B-4403
```

Usage by default UUID:

```bash
./scripts/setup-usb-primary-share.sh
```

Usage by device path:

```bash
./scripts/setup-usb-primary-share.sh /dev/sda1
```

Supported filesystems:

```text
vfat
exfat
ext4
```

### sync-pcs-share-to-backup.sh

Mirrors the USB primary share to the SD-card backup share.

```bash
./scripts/sync-pcs-share-to-backup.sh
```

Sync direction:

```text
/mnt/pcs-usb/PCS-Share -> /srv/pcs-share-backup
```

Warning: this is a mirror-style sync. Files deleted from the USB primary share may also be deleted from the backup mirror.

## PCS Control Panel

### setup-pcs-control-panel.sh

Installs the PCS Control Panel web interface.

```bash
./scripts/setup-pcs-control-panel.sh
```

Control Panel URL:

```text
http://10.42.0.1:8080
```

### setup-dashboard-redirect.sh

Installs the port 80 redirect service.

```bash
./scripts/setup-dashboard-redirect.sh
```

Dashboard URL:

```text
http://10.42.0.1
```

This redirects to:

```text
http://10.42.0.1:8080
```

### pcs-web-action.sh

Allowlisted action dispatcher used by the web control panel.

Installed live path:

```text
/usr/local/sbin/pcs-web-action
```

Repository source:

```text
scripts/pcs-web-action.sh
```

This script should not run arbitrary user-provided shell commands.

## Service Restart

### restart-pcs-services.sh

Restarts core PCS services and prints quick status output.

```bash
./scripts/restart-pcs-services.sh
```

Usually started through:

```text
pcs-restart-services.service
```

## Status and Testing

### pcs-status.sh

Prints detailed PCS system status.

```bash
./scripts/pcs-status.sh
```

Includes:

- Host info
- Time / RTC / Chrony
- Network state
- USB devices
- Samba shares
- Storage paths
- Services
- Client access info

### pcs-self-test.sh

Runs a Pi-side validation test.

```bash
./scripts/pcs-self-test.sh
```

Expected healthy result:

```text
PCS Pi-side self-test PASSED.
```

This is the main quick test after setup, reboot, or major changes.

## Typical Validation

After setup or changes:

```bash
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh
```

From a Windows client:

```cmd
ping 10.42.0.1
ping 8.8.8.8
ping google.com
w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly
```
