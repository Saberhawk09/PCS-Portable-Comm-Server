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
- Client LAN/AP handoff on `eth0`
- Optional Pi-Star coordinated-shutdown pairing at the first usable PCS-LAN point
- RTC
- Samba shares
- Chrony LAN NTP
- Optional WWAN GNSS and LAN-only GPSD sharing
- Optional Pi-Star monitoring and local-access links
- Optional guarded Dire Wolf / SA818S APRS integration
- Optional Meshtastic Bluetooth/MQTT gateway software staging with the service disabled
- PCS restart service
- PCS public homepage and authenticated control panel
- Legacy port 8080 admin compatibility redirect
- Final status/self-test checks

In `ALL` or `DEFAULTS` input mode, the Pi-Star inclusion choice is collected
with the other setup answers. When enabled, the installer configures the PCS
LAN and then immediately attempts coordinated-shutdown pairing before the
remaining setup steps. The earlier answer suppresses the redundant pairing
confirmation; SSH still requests the Pi-Star password directly and never
stores it. If Pi-Star is unavailable, pairing remains an optional failure and
the rest of the PCS installation continues.

## Meshtastic Bluetooth / MQTT

### setup-meshtastic-bluetooth.sh

Stages or configures a persistent USB serial or BLE connection to a dedicated
Meshtastic node and transparently relays its MQTT client-proxy traffic:

```bash
./scripts/setup-meshtastic-bluetooth.sh --prepare
./scripts/setup-meshtastic-bluetooth.sh --scan
./scripts/setup-meshtastic-bluetooth.sh --configure DEVICE MQTT_HOST MQTT_PORT
./scripts/setup-meshtastic-bluetooth.sh --configure-usb /dev/ttyACM0 MQTT_HOST MQTT_PORT
./scripts/setup-meshtastic-bluetooth.sh --check
```

Staging never contacts or configures the radio. Configured operation keeps the
selected transport connected continuously. USB configuration disables the
node's Bluetooth radio and exposes only `/dev/ttyACM0` inside the hardened
service. MQTT downlink starts with no subscriptions and must be given exact
topic filters deliberately. The status snapshot also
exposes temperature/humidity from the local node's environment sensor for a
future PCS case-telemetry display. See [Meshtastic USB/Bluetooth MQTT Gateway](../docs/meshtastic-bluetooth-gateway.md).

## Dependencies

### install-dependencies.sh

Installs baseline packages used by PCS.

```bash
./scripts/install-dependencies.sh
```

Includes tools for networking, Samba, Chrony, GPSD, ModemManager, Cockpit, and general diagnostics.

## GPIO Devices

### pcs_gpio.py

Prints the finalized GPIO map, performs read-only dependency discovery, and
runs guarded commissioning patterns for the LCD, MAX7219 matrix, WS2812 LEDs,
or fan:

```bash
python3 scripts/pcs_gpio.py pins
python3 scripts/pcs_gpio.py check
python3 scripts/pcs_gpio.py demo all --duration 0
python3 scripts/pcs_gpio.py stats
python3 scripts/pcs_gpio.py lcd --line1 "PCS ONLINE" --line2 "LCD DRIVER READY"
python3 scripts/pcs_gpio.py lcd-status
python3 scripts/pcs_gpio.py led-status
python3 scripts/pcs_gpio.py fan-control
```

Simulation is the default. Real writes require both `--hardware` and `--apply`.
The `lcd` command accepts two lines, trims/centers each to 16 characters, and
keeps the written text visible unless `--clear` is requested.
The `lcd-status` command normally rotates six 16x2 pages: PCS state and uptime, CPU
temperature in Celsius/Fahrenheit, active network uplink, cellular state and
signal quality, GPS fix state with paired satellites-in-view/used counts, then
active AP client count and six-character Maidenhead grid square.
Warnings append centered plain-language explanation pages after those six
pages. A hard fault suppresses normal statistics and rotates only centered
critical-fault pages. CPU temperature, root-disk use, primary USB mounting,
failed services, uplink state, and GPS fix use the same conditions and priority
as the matrix annunciator.
The cellular `On`/`Off` state follows NetworkManager's actual data session;
signal quality may remain available while cellular data is disconnected.
PTT and SA818 UART are never driven by this tool. See
[PCS GPIO Allocation](../docs/gpio-allocation.md) for the pin map and hardware
commissioning commands.

### setup-gpio-lcd.sh

Installs or inspects the persistent GPIO-only HD44780 status rotation:

```bash
bash scripts/setup-gpio-lcd.sh --install
bash scripts/setup-gpio-lcd.sh --check
```

The base installer exposes this as the optional `PCS_SETUP_GPIO_LCD=yes|no`
choice, persists the answer in `config/pcs-install.conf`, and restores the
service during a reinstall when selected.

The service uses the same privacy-preserving data collectors as the matrix and
does not retain coordinates or control APRS PTT, radio UART, SPI, fan, or
WS2812 lines.

### setup-gpio-stats.sh

Installs or inspects the persistent SPI-only MAX7219 health annunciator:

```bash
bash scripts/setup-gpio-stats.sh --install
bash scripts/setup-gpio-stats.sh --check
```

The base installer exposes this as the optional
`PCS_SETUP_GPIO_STATS=yes|no` choice. The standalone installer enables SPI0
when necessary, installs `python3-spidev` if missing, and reports when a reboot
is needed before `/dev/spidev0.0` becomes available.

The service shows one dim checkmark when PCS is healthy. Every warning shows an
`!` followed by its subsystem icon; every critical fault shows an `X` followed
by its subsystem icon. It watches CPU temperature, root-disk capacity, primary
USB mounting, failed systemd units, OpenWrt AP reachability, configured Pi-Star
reachability, active uplink, and GPS fix without duplicating the LCD's normal
telemetry. The OpenWrt hard fault uses a Wi-Fi icon; the Pi-Star warning uses a
raspberry icon. It does not retain GPS coordinates or control APRS PTT or radio
UART lines. The healthy checkmark uses intensity 1; all warning and critical
frames use intensity 10, below the MAX7219 maximum of 15.

### setup-gpio-leds.sh

Installs or inspects the persistent six-pixel GPIO21 WS2812 health indicators:

```bash
bash scripts/setup-gpio-leds.sh --install
bash scripts/setup-gpio-leds.sh --check
```

Pixels 0-5 represent CPU temperature, root-disk use, primary USB mounting,
local services/configured Pi-Star, active uplink/OpenWrt AP, and GPS fix. The
base installer persists the optional `PCS_SETUP_GPIO_LEDS=yes|no` answer. The
service uses the PCM output path on GPIO21, leaves GPIO18 PWM fan control
independent, and is compatible with the PCS USB sound adapter; do not combine
it with an I2S/PCM sound device. Wi-Fi and Cellular are both healthy green
uplinks; no uplink is amber, while an unreachable OpenWrt AP is red.
The pinned `rpi-ws281x` package lives in `/opt/pcs-gpio-leds` rather than the
Raspberry Pi OS system Python environment.

The LCD, WS2812, and matrix installers also install and arm the shared
`pcs-gpio-shutdown.service`. During a normal halt, reboot, or poweroff, the
regular display daemons stop first. The shutdown service then leaves
`PCS Offline` / `Shutting Down` on the LCD, turns all six status pixels blue,
and latches a dim bed/ZZZ icon on the matrix. Per-device marker files under
`/etc/pcs/gpio-shutdown` prevent optional hardware that was never installed
from being probed. The final images remain visible only while display power is
still present.

### setup-gpio-fan.sh

Configures GPIO18/physical pin 12 as PWM0 and installs the persistent thermal
fan controller:

```bash
bash scripts/setup-gpio-fan.sh --install
bash scripts/setup-gpio-fan.sh --check
```

The base installer exposes this as the optional `PCS_SETUP_GPIO_FAN=yes|no`
choice. Installation disables the unused onboard analogue audio PWM function,
adds `dtoverlay=pwm,pin=18,func=2`, and normally requires one reboot. Dire
Wolf's separate USB sound adapter is unaffected.

The controller uses the 52Pi-documented 100 Hz PWM frequency and a conservative
five-step curve: 40% below 45 C, then 55/70/85% at 45/55/65 C and 100% at 75 C.
It uses 3 C downshift hysteresis and polls every five seconds. Startup, shutdown,
missing temperature data, or daemon failure leave the PWM channel enabled at
100% duty. The hardware has no tachometer feedback, so configured duty and CPU
temperature are observable but actual fan RPM is not measured.

## Dire Wolf / APRS

### setup-direwolf-aprs.sh

Stages Dire Wolf software and provides guarded rendering,
validation, activation, testing, and rollback:

```bash
./scripts/setup-direwolf-aprs.sh --prepare-uart
./scripts/setup-direwolf-aprs.sh --prepare
./scripts/setup-direwolf-aprs.sh --import-commissioned-profile
./scripts/setup-direwolf-aprs.sh --configure-options
./scripts/setup-direwolf-aprs.sh --record-validation
./scripts/setup-direwolf-aprs.sh --list-audio
./scripts/setup-direwolf-aprs.sh --detect-audio
./scripts/setup-direwolf-aprs.sh --check
./scripts/setup-direwolf-aprs.sh --capabilities
./scripts/setup-direwolf-aprs.sh --software-test
./scripts/setup-direwolf-aprs.sh --render-config rx
./scripts/setup-direwolf-aprs.sh --render-config tx
./scripts/setup-direwolf-aprs.sh --validate-config rx
./scripts/setup-direwolf-aprs.sh --validate-config tx
./scripts/setup-direwolf-aprs.sh --activate-rx
./scripts/setup-direwolf-aprs.sh --activate-tx
./scripts/setup-direwolf-aprs.sh --rollback
./scripts/setup-direwolf-aprs.sh --help
```

Staging records `PCS_SETUP_APRS="staged"` in the ignored local install config,
keeps `direwolf.service` stopped/disabled, and makes the state visible only on
the authenticated dashboard. See [Dire Wolf / APRS Integration](../docs/direwolf-aprs.md).

Flag behavior:

- `--prepare` installs dependencies and the safe template while leaving the service disabled.
- `--prepare-uart` idempotently enables the Pi UART, removes the serial login
  console, disables serial getty units, and leaves Bluetooth unchanged.
- `--import-commissioned-profile` atomically replaces the managed APRS config
  block, removes stale APRS keys, and resets all physical-evidence gates.
- `--configure-options` records non-secret desired settings only.
- `--prepare` installs the Debian service/package foundation and automatically
  builds the official pinned Dire Wolf 1.8.1 source when the distribution
  package does not meet the PCS 1.8+ requirement. It leaves the service disabled.
- `--record-validation` records hardware evidence but never activates the service.
- `--list-audio`, `--check`, and `--capabilities` are read-only discovery/status commands.
- `--detect-audio` records a stable ALSA ID only for one unambiguous USB capture/playback card and resets audio evidence gates.
- `--software-test` uses temporary WAV files to verify AX.25, FX.25, and timing tolerance without RF.
- `--render-config rx|tx` prints a proposed configuration with no real passcode.
- `--validate-config rx|tx` lints the proposal and reports every activation blocker.
- `--activate-rx` installs a transactional receive/IGate profile with null output and no RF transmit directives.
- `--activate-tx` requires all evidence gates and an exact typed RF confirmation before installing the TX profile.
- successful activation refreshes an already-commissioned PCS control panel
  without replacing its credentials.
- `--rollback` restores the newest root-owned live-configuration backup.
- `--help` and `-h` print the terminal command reference.

Activation also installs boot-time SA818S programming, explicit ALSA restoration,
persistent LAN-only AGW/KISS filtering, managed Dire Wolf CSV logging/rotation,
restart-on-device-recovery behavior, and the commissioned independent RF plus
direct APRS-IS GNSS beacon schedules. Full option,
security, and rollback details are in
[Dire Wolf / APRS Integration](../docs/direwolf-aprs.md).

### pcs_aprs_telemetry.py

Summarizes Dire Wolf daily CSV logs for the dashboard:

```bash
./scripts/pcs_aprs_telemetry.py
./scripts/pcs_aprs_telemetry.py --json
./scripts/pcs_aprs_telemetry.py --log-dir /var/log/direwolf
./scripts/pcs_aprs_telemetry.py --help
```

It counts received RF packets for the last hour/day, unique recent stations,
and the last RF packet while excluding Dire Wolf's synthetic channel 999
tracker-transmit rows.

### pcs_sa818.py and pcs-aprs-audio.sh

`pcs-sa818.service` applies the commissioned 144.5500 MHz, 25 kHz, no-tone,
squelch-1, volume-8, filters-off, tail-off profile over `/dev/serial0`, then
requires an exact `AT+DMOREADGROUP` match. `pcs-aprs-audio.service` applies and
verifies Sabrent/C-Media card `Device` at -18 dB playback, 100% capture, and AGC
off. Dire Wolf reruns both helpers before every start so UART or USB
re-enumeration does not bypass the known-good profiles.

### pcs-aprs-kiss-firewall.sh

Normally managed by `pcs-aprs-kiss-firewall.service`:

```bash
sudo /usr/local/sbin/pcs-aprs-kiss-firewall --apply
sudo /usr/local/sbin/pcs-aprs-kiss-firewall --check
sudo /usr/local/sbin/pcs-aprs-kiss-firewall --clear
sudo /usr/local/sbin/pcs-aprs-kiss-firewall --help
```

The helper admits AGW and KISS only from loopback and the configured PCS LAN, then
drops the selected KISS port on all other interfaces.

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
PCS_PISTAR_DISABLE_WIFI=no ./setup-pistar-pcs.sh --apply
sudo reboot
PCS_PISTAR_DISABLE_WIFI=no ./setup-pistar-pcs.sh --check
./setup-pistar-pcs.sh --apply
sudo reboot
./setup-pistar-pcs.sh --check
```

Copy this script to Pi-Star and run it there as the normal Pi-Star user. It
requires 30 continuous seconds of RTL8152 carrier and PCS reachability before
mutation, then manages hostname, the marked `dhcpcd` static-address block on
`eth0`, the final recoverable `disable-wifi` boot overlay, PCS NTP,
Pi-Star's native Wi-Fi boot/AP guards, YSFGateway's GPSD client, and the unused
local MobileGPS path. On the tested Bullseye image it also removes the obsolete
cgroup tmpfs conflict, mirrors Pi-Star's native D-Star mode markers in a systemd
condition, and permits the ARM `uname` syscall in `haveged`'s existing sandbox.
It does not contain, erase, or modify Wi-Fi passwords, callsigns, radio-mode
configuration, or digital-network credentials.

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

Installs the PCS public homepage, Admin Login, authenticated operator controls, local password hash, and systemd services.

```bash
./scripts/setup-pcs-control-panel.sh
```

A fresh interactive install prompts for the admin password. If a password already exists, a repeat install asks whether to keep it or replace it. The authenticated administration page also provides **Change Admin Password** when the current password is known.

Public homepage and Admin Login URLs:

```text
http://10.42.0.1/
http://10.42.0.1/admin/
```

If the password is forgotten, it cannot be reset in the browser. Rerun the installer interactively:

```bash
./scripts/setup-pcs-control-panel.sh --reset-admin-password
```

### setup-dashboard-redirect.sh

Installs or repairs the optional legacy port 8080 compatibility redirect. The unified PCS application owns port 80; old `:8080` bookmarks are redirected to `/admin/`.

```bash
./scripts/setup-dashboard-redirect.sh
```

Legacy URL:

```text
http://10.42.0.1:8080/
```

This redirects to:

```text
http://10.42.0.1/admin/
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
- Dire Wolf / APRS staged or active state
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
