# Portable Comm Server

[![CI](https://github.com/Saberhawk09/PCS-Portable-Comm-Server/actions/workflows/ci.yml/badge.svg)](https://github.com/Saberhawk09/PCS-Portable-Comm-Server/actions/workflows/ci.yml)

A portable communications server built around a Raspberry Pi 4 with dedicated routing, integrated cellular internet, GPS-disciplined NTP, LAN file sharing, web monitoring, and multi-protocol radio interface/hotspot.

What started as an annoyance caused by Windows networking has evolved into my first end-to-end hardware and software project.

## Warning: AI-generated code

### I'm a high school dropout working at a convenience store; of course I'm using AI to help me code.

Codex was heavily utilized in the creation of this project. I have thoroughly tested every aspect of the code and found it to be reliable and repeatable over several wipe/reinstall cycles. If for whatever reason you decide to recreate this project, you *should* be able to utilize this code with minimal changes/addtional LLM use depending on your hardware.

This GitHub will also use a mix of my own writing and AI-generated text. I am learning how to use GitHub, and I'd rather have a nice AI-generated page than a gross-looking human-generated one because I don't know what I'm doing.

## What is PCS?

At its core, initially PCS was little more than a portable Raspberry Pi 4 based networking appliance. It intergrated a Pi 4, old Linksys EA4500 router, and surplus Sierra Wireless cell modem into a box with a USB drive samba share. The power system was 2 seperate AC-DC supplies, and the case was far from ideal. The Pi provides DNS and gateway services, while the EA4500 was simply a dumb AP/switch. The cell modem provided internet where available, and also functioned as a GNSS receiver thanks to its dedicated GPS antenna port.

In its current state, PCS is *much* more feature complete. It intergrates the core features, remote management, web portal, stats API, samba share, AC/DC power system, Pi-Star, Meshtastic, APRS, and diagnostic indicators + power monitoring into one single casette that fits neatly into an Apache 4800 rugged case. With the cellular and GNSS antennas installed in the lid, the entire system only needs opened, connected to power, and turned on. Everything else is automatic, and is fully water tight and very durable when the case is closed.

## Project Goals

- Simple and straightforward software setup with a single script
- Reliable LAN communication between connected clients
- Samba file share reachable by connected clients
- NMEA GPS-disciplined / internet NTP reference for connected clients
- Optional cellular internet connectivity for connected clients
- Multi-Protocol radio interface (APRS, Meshtastic, Pi-Star)
- Rugged and durable enclosure for field deployment
- Emergency/grid power capable
- Easy operation by non-technical users

For more detail, see [Project Overview](docs/project-overview.md).

## Current Status

PCS software is currently beta-quality but working. Pi-side installs are repeatable, and the base installer configures the core network, storage, time, monitoring, WWAN/GNSS, and optional APRS, Pi-Star, and Meshtastic support. Modem firmware or USB-composition changes, credentials, unavailable external appliances, radio identity, and RF activation remain deliberate operator-supervised steps.

The PCS hardware is an operational v1 prototype. The AC/DC source selector,
cooling fans, Pi-Star hotspot, cellular/GNSS path, external SMA antennas,
HD44780 LCD, MAX7219 matrix, WS2812 indicators, dual INA226 power monitors,
passive buzzer, and APRS subsystem are installed.

The SA818S, Easy Digi, GPIO6 PTT, USB audio, GNSS
beaconing, two-way APRS-IS, messaging, and WIDE1-1 fill-in operation have been
validated. The RAK4631 persistent USB/MQTT gateway and GPSD position delivery
are live-validated, including successful encrypted proxy publishes to the
NeoMesh broker, opted-in hourly map reporting, public-map appearance for IJC1,
and RF-to-public-map forwarding of an opted-in IJC2 position heard over LoRa.
PCS sends its GPSD-backed Meshtastic position every 30 minutes with 15-bit
channel/map precision. The exact as-built electrical and mechanical record is
also unfinished.

### Hardware

- Raspberry Pi 4 8GB service host and network gateway
- Linksys EA4500 running OpenWrt as the AP and Ethernet switch
- Sierra Wireless EM7565 connected through a USB WWAN adapter
- LTE and active GNSS antennas with validated cellular registration and 3D GNSS fixes
- DS1307 I2C RTC for a sane boot-time reference
- Removable USB primary storage with an SD-card backup mirror
- Optional Pi-Star hotspot integrated at `10.42.0.3`
- AC/DC power system with source selector switch
- INA226 power monitoring modules on the input and 5v rails
- HD44780 16x2 Charactor LCD, MAX7219 8x8 LED Matrix annunciator, and six-pixel WS2812 RGB LED status chain
- GPIO18 hardware-PWM fan control; commanded duty is validated but RPM is not measured
- SA818S VHF Radio Module/Easy Digi APRS subsystem with Sabrent USB audio, GPIO6 PTT, direct UART control, and validated bidirectional RF/APRS-IS operation
- RAK4631 Meshtastic expansion connected over USB with validated persistent
  NeoMesh MQTT proxy and 30-minute GPSD position delivery

### Software

- Reliable unattended startup after power-on or reboot, with bounded GPIO boot indicators before live health alerts
- Public PCS status homepage and password-protected administration at `10.42.0.1`
- Pi-side self-test and status scripts
- Commissioned dual-INA226 input/5V monitoring, estimated non-5V load,
  since-boot mAh/Wh totals, and guarded nominal-12V low-voltage shutdown
- Active-low GPIO13 passive-buzzer patterns with alarm priority, debounced
  visual-health alert mirroring, mute control, and validated startup,
  shutdown, warning, fault, and low-voltage behavior
- USB primary Samba share with SD-card backup mirror
- GPS NMEA from `/dev/ttyUSB1` through gpsd and Chrony to LAN clients
- Installer-selectable manual cellular control or automatic Wi-Fi-to-cellular
  fallback; the commissioned PCS uses automatic mode, with live failover,
  cellular-only Internet, Wi-Fi recovery, and boot persistence validated
- LAN GPSD, NTP, and installer-assisted coordinated Pi-Star shutdown integration
- Installer-selectable Dire Wolf or Graywolf APRS engine with SA818S
  programming, ALSA level restoration, LAN-only AGW/KISS access, mutual
  exclusion, guarded activation/rollback, and stuck-PTT protection; the
  commissioned PCS currently uses Dire Wolf while retaining Graywolf as a
  masked alternative
- Repeatable Meshtastic USB/BLE MQTT gateway with privacy-safe public/admin
  dashboard status, guarded restart, broker/proxy policy validation, GPSD
  position delivery, public-map forwarding status, and local environment telemetry
- PCS Pi SD-card wipe/rebuild most recently verified on August 18, 2026; the
  v1.8 power/alarm/shutdown stack completed supervised appliance acceptance on
  September 7, 2026; credentials, external-device recovery, and RF checks
  remain manual

### Current Finish Work

- Capture final enclosure dimensions, mounting details, photos, and CAD references
- Reconcile the power and wiring documents with the physical as-built system
- Complete the detailed as-built power wiring, fuse, and thermal record; add a
  second reference-load point if tighter current calibration is needed
- Continue expanding automated and operator-supervised field validation
- Resolve RF coupling into the shared I2C bus: SA818S key-down currently causes
  immediate INA226 transaction errors and may temporarily interrupt RTC access.
  The devices recover after unkeying and no RTC timekeeping damage is observed,
  but monitored extended key-down remains unverified.

## Hardware Setup

Before running setup, connect the hardware you want the installer to configure:

- Raspberry Pi booted from the target SD card. Raspberry Pi OS 64-bit Desktop is validated; the installer also supports a headless Raspberry Pi OS Lite 64-bit path, whose first full appliance wipe acceptance remains pending.
- Ethernet from the Pi to the PCS router/AP through a LAN port, not the WAN/Internet port.
- The PCS router/AP powered on.
- The RTC module installed, if this build includes the RTC.
- The WWAN modem installed and connected over USB, if this build includes cellular/GPS.
- The GPS/GNSS antenna connected to the WWAN modem, if configuring WWAN GPS/NMEA.
- The intended USB storage device connected, if using USB primary file storage.
- The 16x2 HD44780 LCD, six-pixel WS2812 indicator chain, and MAX7219 matrix
  connected to their documented GPIO lines, when fitted.
- The GPIO18 PWM fan connected, when using the Armor Lite cooler.
- The APRS USB sound card and radio interface, only when moving beyond software staging.

## Software Setup

If WireGuard remote management will be enabled during setup, add its private
profile before starting the installer. Simply place `wg-pcs.conf` in `/home/pi`.
Setup discovers it and offers the path as the prompt default; press Enter to
accept it, or enter another path. Restrict the file to the `pi` account:

```bash
sudo chown pi:pi /home/pi/wg-pcs.conf
chmod 600 /home/pi/wg-pcs.conf
```

Other owners, symlinks, and exposed permissions are rejected.
Setup also asks for the trusted home Wi-Fi subnet (for example,
`192.168.50.0/24`) if you want management through that network. WireGuard
activation refuses to apply a policy that would block the current SSH source.

When Dire Wolf APRS is selected, setup asks separately for the base callsign,
SSID, and APRS-IS passcode. The passcode is used only to render the protected
live Dire Wolf configuration; it is not written to `pcs-install.conf` or Git.

```bash
sudo apt-get update
sudo apt-get install -y git
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/Saberhawk09/PCS-Portable-Comm-Server.git
cd PCS-Portable-Comm-Server
```

Run the base setup:

```bash
./scripts/setup-pcs-base.sh
```

Once setup is completed, you will see a FAILS related to the RTC and other hardware/software. This is expected and fixed with a reboot.


## After Setup

After setup completes, reboot:

```bash
sudo reboot
```

After the Pi comes back up:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
git status
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh
```

Expected result:

```text
nothing to commit, working tree clean
PCS Pi-side self-test PASSED.
```

An intentionally disconnected cellular profile is informational when manual
mode is selected or Wi-Fi is active in automatic-fallback mode. Investigate
every reported warning or failure before field use.

For more detail, see [Testing Checklist](docs/testing-checklist.md).
The setup script installs the PCS software baseline, configures the Pi client network, and sets up Samba, Chrony, RTC support, Cockpit, the public PCS homepage, and the authenticated administrative control panel. When selected, it also configures Pi-Star monitoring and coordinated-shutdown pairing, can safely stage either Dire Wolf or Graywolf without activating an RF path, can stage the persistent Meshtastic USB/Bluetooth MQTT gateway without connecting to a radio, and can install the 16x2 HD44780 status display, six-pixel WS2812 indicators, MAX7219 annunciator, and GPIO18 hardware-PWM thermal fan controller.

For more detail, see [Raspberry Pi Setup](docs/raspberry-pi-setup.md) and [Script Reference](scripts/README.md).



## PCS Documentation

For additional documentation, start here:

- [Project Overview](docs/project-overview.md)
- [Bill of Materials](docs/bill-of-materials.md)
- [Power System](docs/power-system.md)
- [Network Topology](docs/network-topology.md)
- [Network Design](docs/network-design.md)
- [WireGuard Remote Management](docs/wireguard-remote-management.md) - commissioned outbound management tunnel
- [Remote Management, PCS API, and Android Companion Roadmap](docs/remote-management-api-android-roadmap.md)
- [PCS Companion for Android](android/pcs-companion/README.md) - local app source, build, security, and device-test status
- [Raspberry Pi Setup](docs/raspberry-pi-setup.md)
- [Full-Stack Reinstall Runbook](docs/full-stack-reinstall.md)
- [WWAN Card Setup](docs/wwan-card-setup.md)
- [GPS Network Sharing](docs/gps-network-sharing.md)
- [GPS, Internet NTP, and RTC Time Sources](docs/time-sources.md)
- [Pi-Star Integration](docs/pi-star-integration.md)
- [Meshtastic USB/Bluetooth MQTT Gateway](docs/meshtastic-bluetooth-gateway.md)
- [Samba File Share](docs/samba-file-share.md)
- [PCS Control Panel](docs/pcs-control-panel.md)
- [PCS GPIO Allocation](docs/gpio-allocation.md)
- [Testing Checklist](docs/testing-checklist.md)
- [Release Checklist](docs/release-checklist.md)
- [Script Reference](scripts/README.md)
- [Changelog](CHANGELOG.md)

## Quick Client Access

From a device connected to the PCS network:

```text
PCS Homepage:       http://10.42.0.1/
PCS Admin Login:    http://10.42.0.1/admin/
Cockpit:            https://10.42.0.1:9090
Primary Share:      \\10.42.0.1\PCS-Share
Backup Share:       \\10.42.0.1\PCS-Backup
LAN NTP Server:     10.42.0.1
LAN GPSD Server:    10.42.0.1:2947 (optional)
Pi-Star Dashboard:  http://10.42.0.3 (when selected during setup)
```

Windows NTP test:

```cmd
w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly
```

Windows File Explorer tests:

```text
\\10.42.0.1\PCS-Share
\\10.42.0.1\PCS-Backup
```

For more detail, see [PCS Control Panel](docs/pcs-control-panel.md) and [Samba File Share](docs/samba-file-share.md).


## Cellular Connection Note

Fresh installs ask whether cellular should remain manual or automatically serve
as a fallback when Wi-Fi is unavailable; the conservative default is manual.
The selected policy is saved in `config/pcs-install.conf`.

In `wifi-fallback` mode, PCS waits 30 seconds after active Wi-Fi is lost before
starting cellular. After Wi-Fi has been restored for 30 seconds, it disconnects
only a cellular session that the fallback service started itself. The
NetworkManager profile remains non-autoconnecting in both modes, and manually
started cellular sessions remain under operator control.

Change the installed policy at any time:

```bash
./scripts/setup-cellular-profile.sh --fallback wifi-fallback
./scripts/setup-cellular-profile.sh --fallback manual
```

Open the PCS homepage and select **Admin Login**:

```text
http://10.42.0.1/
```

Authenticated operators can change the admin password from the administration page. If it is forgotten, rerun `./scripts/setup-pcs-control-panel.sh --reset-admin-password` from an interactive Pi terminal.





## Important Scripts

- [`scripts/setup-pcs-base.sh`](scripts/setup-pcs-base.sh) - Main baseline setup workflow
- [`scripts/pcs-self-test.sh`](scripts/pcs-self-test.sh) - Quick Pi-side validation test
- [`scripts/pcs-status.sh`](scripts/pcs-status.sh) - Detailed system status output
- [`scripts/setup-usb-primary-share.sh`](scripts/setup-usb-primary-share.sh) - Configure USB storage as `PCS-Share`
- [`scripts/sync-pcs-share-to-backup.sh`](scripts/sync-pcs-share-to-backup.sh) - Mirror USB primary share to SD backup
- [`scripts/setup-pcs-backup.sh`](scripts/setup-pcs-backup.sh) - Install the validated automatic-backup policy and timer
- [`scripts/setup-pcs-control-panel.sh`](scripts/setup-pcs-control-panel.sh) - Install the public homepage and authenticated control panel
- [`scripts/setup-dashboard-redirect.sh`](scripts/setup-dashboard-redirect.sh) - Install the legacy port 8080 redirect to `/admin/`
- [`scripts/setup-gpsd-lan-proxy.sh`](scripts/setup-gpsd-lan-proxy.sh) - Publish GPSD only on the trusted PCS LAN
- [`scripts/setup-chrony-lan-ntp.sh`](scripts/setup-chrony-lan-ntp.sh) - Configure GPS-first, Internet-second LAN NTP with RTC holdover
- [`scripts/setup-rtc.sh`](scripts/setup-rtc.sh) - Configure the DS1307 and guarded boot-time RTC seed
- [`scripts/setup-pistar-pcs.sh`](scripts/setup-pistar-pcs.sh) - Apply or verify the Pi-Star PCS integration
- [`scripts/setup-pistar-shutdown.sh`](scripts/setup-pistar-shutdown.sh) - Pair the PCS shutdown button with Pi-Star
- [`scripts/setup-direwolf-aprs.sh`](scripts/setup-direwolf-aprs.sh) - Stage, validate, activate, or recover the managed APRS subsystem
- [`scripts/setup-graywolf-aprs.sh`](scripts/setup-graywolf-aprs.sh) - Safely stage the pinned Graywolf APRS alternative without activating hardware or RF
- [`scripts/pcs-graywolf-profile.py`](scripts/pcs-graywolf-profile.py) - Provision and read back a disabled Dire Wolf-equivalent Graywolf migration profile
- [`scripts/setup-meshtastic-bluetooth.sh`](scripts/setup-meshtastic-bluetooth.sh) - Stage or configure the persistent Meshtastic USB/BLE MQTT gateway
- [`scripts/setup-wireguard-management.sh`](scripts/setup-wireguard-management.sh) - Opt-in WireGuard profile import and commissioned management workflow
- [`scripts/pcs_gpio.py`](scripts/pcs_gpio.py) - Inspect, simulate, and deliberately test PCS GPIO status hardware

See [Script Reference](scripts/README.md) for the full script list.

## Hardware and Expansion Status

Installed and tested hardware:

- Raspberry Pi 4
- Armor Lite cooler with GPIO18 hardware-PWM fan control
- RTC module
- Generic USB flash drive
- Linksys EA4500 running OpenWrt used as AP/switch
- Sierra Wireless EM7565 WWAN modem with external LTE and active GNSS antennas
- MMDVM Pi-Star hotspot w/ Pi Zero W
- AC/DC source-selector power system and two 120 mm cooling fans
- HD44780 16x2 LCD status display
- MAX7219 8x8 LCD Matrix health-annunciator
- Six-pixel WS2812 RGB LED status-indicator chain
- SA818S VHF Radio, Easy Digi Audio Interface, Sabrent USB audio device, GPIO6 PTT, and managed Dire Wolf APRS
- RAK4631 Meshtastic node over USB with NeoMesh MQTT, GPSD position, and public-map forwarding
- Dual INA226 input/5V power monitoring and GPIO13 passive buzzer

Remaining documentation and validation:

- Complete the exact as-built power-component, fuse, wiring, grounding, and
  thermal record; retain the measured rail and load references already captured
- Capture final enclosure dimensions, mounting details, photographs, and CAD/export references

## The Issue That Started This Project

As usual, when multi-billion dollar companies fail to understand how to code their software properly, open source comes to the rescue yet again.

During ARRL Field Day 2026 me and my amateur radio club ran into some networking issues. We had 3 Windows laptops running radio contact logging software for the event, and wanted all 3 to use the same log file for accurate tracking of stats.

The problem was simple:

Windows update.

During the Winter Field Day prior, we had used my Dell Latitude 7212 Toughbook for networking. All logging machines connected to my machine through the Wi-Fi hotspot function, and it worked well enough. The only problem was Windows not allowing me to turn the hotspot on without an internet connection. My Toughbook had a DW5821e cellular modem, but the signal was very marginal, so I had to make sure that once the hotspot was on, it never turned off.

Nerve wracking and annoying, but we made it work.

However.

With Windows being Windows, this suboptimal but functional solution never worked again.

It was an hour before start time, while everyone was setting up antennas I was configuring the network share. Only trouble was, Windows had other ideas. Devices couldn't connect to my hotspot, once connected my Toughbook never showed they were, they didn't have internet access, and I could never see my Toughbook share over the local network. It was a massive headache that was thankfully solved by a club member who let us use his portable cellular router while we sourced a replacement dedicated club router.

Once we had everything hooked up via Ethernet, all the file sharing worked and we never had a single issue with networking or the rest of the event. Needless to say I was annoyed. Not just at Windows, but at myself for assuming it would work properly and not planning ahead. Well the lessons from that mistake have evolved into this project.

The goal of this project isn't to replace commercial networking equipment or build a portable homelab grade server, it's to build a communications appliance specifically tailored to emergency communications exercises and other portable operations.
