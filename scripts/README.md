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
- Optional WWAN GNSS and LAN-only GPSD sharing
- Optional Pi-Star monitoring and local-access links
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

### setup-wwan-gps-nmea.sh

Configures the tested WWAN modem-style GPS path:

WWAN modem -> /dev/ttyUSB1 NMEA -> gpsd -> Chrony

Run after the WWAN USB adapter, modem, and GPS antenna are installed:

./scripts/setup-wwan-gps-nmea.sh

This installs/configures:

- pcs-wwan-gps-nmea.service
- gpsd on /dev/ttyUSB1
- Chrony SHM refclock 0 for GPS-backed LAN NTP

It does not change AT!USBCOMP.

For EM7565 GPS troubleshooting, check for active antenna bias at the GPS SMA. The known-good PCS bench setup measured about 3.1-3.3 V. If NMEA is present but GPS cannot get a fix, check `AT+WANT=1`, GPSSEL/RF path selection, and the MHF4-to-SMA pigtail before changing gpsd or Chrony.

### pcs-wwan-gps-nmea-start.py

Helper used by pcs-wwan-gps-nmea.service.

It enables modem GPS through ModemManager, sends GPS_START to /dev/ttyUSB1, and verifies that NMEA output is present. It hides live location in service logs.

### setup-gpsd-lan-proxy.sh

Optionally exposes the local GPSD service to trusted PCS LAN clients at:

```text
10.42.0.1:2947
```

```bash
bash ./scripts/setup-gpsd-lan-proxy.sh
```

The script uses `systemd-socket-proxyd` bound specifically to the PCS LAN address. GPSD stays on `127.0.0.1:2947`, so the live position feed is not opened on WWAN or other uplink interfaces. Native GPSD clients can use this endpoint directly; see [GPS Network Sharing](../docs/gps-network-sharing.md) for raw-NMEA adapter examples.

Pi-Star 4.2.3 can consume this through YSFGateway's native `[GPSD]` configuration. Do not send raw NMEA to Pi-Star UDP port 7834; that port belongs to Pi-Star's legacy local-serial MobileGPS path and is not a raw-NMEA listener in this build.

The base installer can run this step when `PCS_SETUP_GPSD_LAN=yes` is selected
or present in `config/pcs-install.conf`.

### setup-pistar-pcs.sh

Configures the tested Pi-Star 4.2.3 hotspot as a fixed PCS node:

```bash
./setup-pistar-pcs.sh --apply
sudo reboot
./setup-pistar-pcs.sh --check
```

Copy this script to Pi-Star and run it there as the normal Pi-Star user. It
manages hostname, the marked `dhcpcd` static-address block, PCS NTP,
YSFGateway's GPSD client, and the unused local MobileGPS path. It does not
contain or modify Wi-Fi passwords, callsigns, or digital-network credentials.

See [Full-Stack Reinstall Runbook](../docs/full-stack-reinstall.md).

Pi-Star monitoring is controlled separately by `PCS_SETUP_PISTAR=yes|no` in
`config/pcs-install.conf`. When set to `no`, PCS does not probe the hotspot,
does not display Pi-Star-specific dashboard fields, and does not warn when
`10.42.0.3` is absent.

### setup-pistar-shutdown.sh

Pairs the PCS shutdown button with a configured Pi-Star hotspot:

```bash
./scripts/setup-pistar-shutdown.sh --apply
```

The script asks SSH for the Pi-Star password once, creates a dedicated
root-owned PCS key, and replaces the temporary key entry with a restricted
entry that permits only:

```text
check
poweroff
```

The Pi-Star password is never read by the script, written to the install
configuration, or stored on disk. Verify an existing pairing without shutting
anything down:

```bash
./scripts/setup-pistar-shutdown.sh --check
```

If Pi-Star is unavailable when the dashboard shutdown button is pressed, PCS
prints a warning and continues its own clean shutdown.

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

The script disables generated competing `eth0` profiles such as `netplan-eth0`, gives the PCS shared profile a higher autoconnect priority, and activates it immediately when link is present.

The name still references router WAN sharing, but the current preferred topology uses the attached router/AP as a bridge/AP/switch.

### setup-cellular-profile.sh

Creates or updates the manual T-Mobile cellular profile:

```text
pcs-cellular-profile
```

The profile uses APN `fast.t-mobile.com`, route metric `900`, and autoconnect disabled. Cellular data is connected manually from the PCS Control Panel.

Fresh installs default to `pcs-cellular-profile`. Override the name, APN, or
route metric in `config/pcs-install.conf` with `PCS_CELLULAR_PROFILE`,
`PCS_CELLULAR_APN`, and `PCS_CELLULAR_ROUTE_METRIC`. Older installs that still
have `pcs-cellular-tmobile` remain supported by the status, self-test, and web
action scripts.

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
