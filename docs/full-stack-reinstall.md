# Full-Stack Reinstall Runbook

This runbook rebuilds the tested PCS field LAN:

```text
PCS Raspberry Pi: 10.42.0.1
OpenWrt EA4500:    10.42.0.2
Pi-Star hotspot:   10.42.0.3
DHCP clients:      10.42.0.100-10.42.0.200
```

It separates reproducible project settings from credentials and radio identity.
The repository recreates PCS services and the Pi-Star network/time/GPS
integration. OpenWrt and Pi-Star native backups preserve settings that should
not be committed, such as Wi-Fi passwords, callsigns, and network credentials.

## Hardware Setup

Before starting, have these items connected and available:

- Raspberry Pi 4, target SD card, RTC, WWAN modem, GNSS antenna, and intended
  USB storage
- Linksys EA4500 running the supported OpenWrt build
- Pi-Star hotspot and its target SD card
- Ethernet from the PCS Pi to an EA4500 LAN port
- A workstation that can join the PCS Wi-Fi network
- A temporary internet connection for package installation

Do not use the EA4500 WAN/Internet port unless it has deliberately been added
to the LAN bridge.

## Back Up Before Reimaging

These backups can contain secrets. Store them on trusted removable media or in
an access-controlled folder; do not commit them to this repository.

### PCS

Save:

- `config/pcs-install.conf`
- any files that exist only on `PCS-Share`
- any intentionally edited local service configuration

The installer never writes the Samba password to `pcs-install.conf`. Record
that password separately.

### OpenWrt

In LuCI, open **System > Backup / Flash Firmware**, generate a backup archive,
and save it. The restored configuration must still meet these PCS invariants:

- management address `10.42.0.2/24`
- gateway and DNS `10.42.0.1`
- DHCP server disabled
- Wi-Fi and Ethernet bridged into the LAN

### Pi-Star

Use Pi-Star's **Backup/Restore** page to download a native configuration backup.
This preserves radio identity, modes, and network-account settings. The PCS
integration script deliberately does not copy those values into Git.

## Software Setup

### 1. Rebuild the PCS Raspberry Pi

Install the tested Raspberry Pi OS, create the normal `pi` account, boot, and
clone the repository:

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/Saberhawk09/PCS-Portable-Comm-Server.git
cd PCS-Portable-Comm-Server
```

Run the base installer:

```bash
./scripts/setup-pcs-base.sh
```

For the current GPS-sharing build, select:

```text
Configure WWAN modem NMEA GPS:       yes
Share GPSD with trusted PCS clients: yes
Include Pi-Star in PCS monitoring:   yes
```

The generated `config/pcs-install.conf` should therefore contain:

```bash
PCS_SETUP_WWAN_GPS=yes
PCS_SETUP_GPSD_LAN=yes
PCS_SETUP_PISTAR=yes
```

The GPSD setting installs a socket proxy bound only to
`10.42.0.1:2947`; GPSD itself remains localhost-only.

`PCS_SETUP_PISTAR=yes` enables the hotspot health checks and local-access
links. Set it to `no` on builds without Pi-Star; the dashboard and self-test
then omit those optional checks without degrading overall PCS health.

Reboot:

```bash
sudo reboot
```

### 2. Restore or Configure OpenWrt

If OpenWrt was reimaged, restore the saved archive through LuCI. If no backup
exists, follow [Linksys EA4500 OpenWrt AP Setup](linksys-ea4500-ap.md).

Confirm from the PCS Pi:

```bash
ping -c 2 10.42.0.2
```

### 3. Rebuild Pi-Star

Flash the supported Pi-Star image. Restore the native Pi-Star backup, or
configure the hotspot's callsign, radio modes, Wi-Fi SSID, and service
credentials through the Pi-Star dashboard.

First connect Pi-Star to the PCS Wi-Fi network. Before the PCS script is
applied, it may receive an address in the dynamic range. Find that temporary
address from the PCS dashboard or on the PCS Pi:

```bash
ip neigh show dev eth0
```

From a trusted workstation, copy the integration script to that temporary
address:

```bash
scp scripts/setup-pistar-pcs.sh pi-star@PISTAR_TEMPORARY_IP:~/setup-pistar-pcs.sh
```

On Pi-Star:

```bash
chmod +x ~/setup-pistar-pcs.sh
~/setup-pistar-pcs.sh --apply
sudo reboot
```

The script is idempotent and manages only:

- hostname `pcs-hotspot`
- the marked `dhcpcd` block for `10.42.0.3/24`
- gateway, DNS, and preferred NTP server `10.42.0.1`
- YSFGateway's native GPSD client at `10.42.0.1:2947`
- disabling the unused local-serial MobileGPS path

It backs up every file it changes under `/root/pcs-pistar-backups/`, restores
Pi-Star's root filesystem to read-only when it found it read-only, and leaves
Wi-Fi credentials and radio settings untouched.

After Pi-Star has rebooted at `10.42.0.3`, return to PCS and pair coordinated
shutdown:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/setup-pistar-shutdown.sh --apply
```

Enter the Pi-Star password when SSH asks. The password is used only to install
a restricted shutdown key and is not saved. If Pi-Star was already configured
and reachable during the PCS base installer, this optional pairing step may
already be complete.

## Verification

On PCS:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh
./scripts/setup-pistar-shutdown.sh --check
```

Copy a fresh version of the Pi-Star script after a repository update, then run
on Pi-Star:

```bash
~/setup-pistar-pcs.sh --check
```

The check verifies configuration without printing coordinates. It also requests
the GPSD `VERSION` response across the LAN. A temporary time-sync or GPSD
warning immediately after boot can be retried after a minute.

From a PCS client, verify:

```text
PCS dashboard:     http://10.42.0.1
OpenWrt LuCI:      http://10.42.0.2
Pi-Star dashboard: http://10.42.0.3
```

Finally, cold-boot all three devices and repeat both checks. A reinstall test is
complete when:

- PCS self-test has no failures
- Pi-Star integration check has no configuration failures
- OpenWrt and Pi-Star retain `.2` and `.3`
- Pi-Star time synchronizes through PCS
- Pi-Star receives a GPSD protocol response from PCS
- coordinated shutdown readiness check passes
- required radio modes pass an operator-supervised on-air test

## Remaining Manual Checkpoints

The setup is repeatable, but intentionally not credential-free or fully
unattended. These actions remain manual:

- flashing SD cards and OpenWrt firmware
- entering or restoring Wi-Fi, callsign, and radio-network credentials
- entering the Samba password
- selecting the correct USB storage device if detection is ambiguous
- validating RF behavior on air

These checkpoints prevent secrets from entering Git and prevent an installer
from guessing hardware or radio identity.
