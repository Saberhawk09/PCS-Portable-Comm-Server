# Raspberry Pi Setup

This document describes the Raspberry Pi side of PCS.

The Raspberry Pi is the main PCS server and network controller. It provides LAN services, file sharing, time service, modem/GPS support, the PCS Control Panel, and system status tools.

## Expected Pi Role

The Pi provides:

- PCS LAN gateway at `10.42.0.1`
- DHCP/DNS for PCS clients
- LAN routing and optional internet sharing
- Samba file shares
- Chrony LAN NTP
- Raspberry Pi RTC support
- GPSD support for WWAN GNSS
- PCS Control Panel
- dashboard redirect
- Cockpit access
- status and self-test scripts

The Linksys EA4500 running OpenWrt acts as the access point and switch at `10.42.0.2`.

## Repository Path

The expected repository path on the Pi is:

```bash
/home/pi/Projects/PCS-Portable-Comm-Server
```

Clone with:

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/Saberhawk09/PCS-Portable-Comm-Server.git
cd PCS-Portable-Comm-Server
```

## Base Setup

Run the base setup script:

```bash
./scripts/setup-pcs-base.sh
```

The base setup configures the PCS software baseline, including:

- required packages
- Pi Ethernet LAN profile
- Samba shares
- USB primary share support
- SD-card backup share
- Chrony LAN NTP
- RTC support
- Cockpit
- PCS Control Panel
- dashboard redirect
- status/self-test tooling
- systemd service support

After setup completes, reboot:

```bash
sudo reboot
```

After reboot:

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

Expected self-test summary:

```text
Fail: 0
Warn: 0
```

Cellular data is intentionally manual. The setup creates the cellular profile, but it does not connect cellular data automatically.

## Network Configuration

Current PCS LAN:

```text
PCS subnet:             10.42.0.0/24
Raspberry Pi eth0:      10.42.0.1
OpenWrt AP / switch:    10.42.0.2
Client DHCP range:      10.42.0.x
```

Expected client settings:

```text
IPv4 Address:      10.42.0.x
Subnet Mask:       255.255.255.0
Default Gateway:   10.42.0.1
DNS Server:        10.42.0.1
NTP Server:        10.42.0.1
```

Physical connection:

```text
Raspberry Pi eth0 -> Linksys EA4500 LAN port
```

The access point should have DHCP disabled. The Pi should be the only DHCP server on the PCS LAN.

## Client Access

From a device connected to the PCS network:

```text
PCS Dashboard:      http://10.42.0.1
PCS Control Panel:  http://10.42.0.1:8080
Cockpit:            https://10.42.0.1:9090
Primary Share:      \\10.42.0.1\PCS-Share
Backup Share:       \\10.42.0.1\PCS-Backup
LAN NTP Server:     10.42.0.1
OpenWrt AP:         http://10.42.0.2
```

## Storage Layout

Current Samba storage:

```text
\\10.42.0.1\PCS-Share   -> /mnt/pcs-usb/PCS-Share
\\10.42.0.1\PCS-Backup  -> /srv/pcs-share-backup
```

`PCS-Share` is the primary working field share on removable USB storage.

`PCS-Backup` is the SD-card backup mirror.

Manual sync:

```bash
./scripts/sync-pcs-share-to-backup.sh
```

## Time Services

PCS uses Chrony to provide LAN NTP service.

LAN clients should use:

```text
10.42.0.1
```

Windows NTP test:

```cmd
w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly
```

The Pi also has RTC support so it can boot with sane time before GPS or internet time is available.

## GPS / GNSS

PCS supports WWAN GNSS through Sierra Wireless / Semtech modem NMEA output.

Current tested behavior:

```text
GPS NMEA: /dev/ttyUSB1
gpsd:     receives WWAN GPS NMEA
Chrony:   sees GPS source for LAN NTP
```

The WWAN modem must be manually configured before use.

GPS setup script:

```bash
./scripts/setup-wwan-gps-nmea.sh
```

The PCS project has validated this generic WWAN GPS path with the current modem hardware.

Current EM7565 GPS baseline:

```text
GPS/NMEA port:      /dev/ttyUSB1
Expected GPS bias:  about 3.1-3.3 V at the GPS SMA
Active antenna:     requires GNSS antenna power, such as AT+WANT=1
Known issue found:  bad/open/poorly seated MHF4 GPS pigtail can allow NMEA but prevent a valid fix
```

If NMEA appears but GPS never gets a valid fix, check the active GPS antenna bias voltage and the MHF4-to-SMA pigtail before changing gpsd or Chrony.

For more detail, see [EM7565 GPS / GNSS Notes](em7565-gps-notes.md).

## Cellular Data

Cellular data is intentionally manual.

The modem and GPS may be detected and configured, but the cellular data connection is not expected to auto-connect after setup.

Use the PCS Control Panel:

```text
http://10.42.0.1:8080
```

Manual cellular control avoids:

- surprise data usage
- unwanted reconnect behavior
- confusing automatic state changes
- field troubleshooting headaches

PCS should remain useful without cellular service.

Local LAN, Samba, dashboard, Cockpit, RTC, and LAN NTP should still function without internet.

## Cockpit

Cockpit is available at:

```text
https://10.42.0.1:9090
```

Use Cockpit for system inspection and manual service management when needed.

## Dashboard / Control Panel

Dashboard redirect:

```text
http://10.42.0.1
```

PCS Control Panel:

```text
http://10.42.0.1:8080
```

The Control Panel is the preferred operator interface for PCS service control and status.

## Useful Commands

Run from the repository root:

```bash
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh
./scripts/restart-pcs-services.sh
./scripts/sync-pcs-share-to-backup.sh
```

Check key services:

```bash
systemctl status chrony
systemctl status smbd
systemctl status gpsd
systemctl status cockpit
```

Check network state:

```bash
ip addr
ip route
nmcli con show
nmcli dev status
```

Check clients seen by the Pi:

```bash
ip neigh show dev eth0
```

Clear stale neighbor entries:

```bash
sudo ip neigh flush dev eth0
```

## Reboot Validation

After every major setup change:

```bash
sudo reboot
```

Then:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh
```

A healthy baseline should pass Pi-side self-test with no failures.
