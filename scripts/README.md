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
- Automatic USB-primary to SD-backup scheduling with a configurable policy
- Chrony LAN NTP
- Optional WWAN GNSS and LAN-only GPSD sharing
- Optional Pi-Star monitoring and local-access links
- Optional guarded Dire Wolf / SA818S APRS integration
- Optional Meshtastic USB/Bluetooth MQTT gateway software staging with the service disabled
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

WireGuard remote management is an explicit, default-off base-installer choice.
The implementation was commissioned on PCS on 2026-08-26 after supervised
home-hub, peer-isolation, cellular, routed-LAN, and reboot-recovery tests.

## WireGuard Remote Management

### setup-wireguard-management.sh

Manages the opt-in outbound management client. For base setup, first place the
private client export at ignored path `private-config/wg-pcs.conf`; the
installer validates it before installing anything and requires a real
home-hub handshake before accepting activation.

```bash
./scripts/setup-wireguard-management.sh --prepare
./scripts/setup-wireguard-management.sh --validate-profile private-config/wg-pcs.conf
./scripts/setup-wireguard-management.sh --import-profile private-config/wg-pcs.conf
./scripts/setup-wireguard-management.sh --generate-key
./scripts/setup-wireguard-management.sh --validate-config
./scripts/setup-wireguard-management.sh --configure
./scripts/setup-wireguard-management.sh --activate
./scripts/setup-wireguard-management.sh --check
./scripts/setup-wireguard-management.sh --deactivate
./scripts/setup-wireguard-management.sh --rollback
./scripts/setup-wireguard-management.sh --help
```

Preparation and configuration keep the tunnel/firewall disabled. Activation
requires confirmation, rejects broad routes, applies isolation first, proves
that no default route uses `wg-pcs`, and requires an authenticated handshake or
rolls back. Deactivation preserves runtime inputs; rollback removes feature
files but deliberately preserves private and pre-shared key material plus the
deployment-local config.
The profile importer accepts ASUS `DNS` and `PresharedKey` fields. DNS is
narrowly validated but never applied; the PSK is stored separately as a
root-only mode-`0600` secret and rendered only into the protected tunnel file.
Command hooks, Table/MTU changes, extra peers, symlinks, and files readable by
another user remain rejected.

### pcs-wireguard-firewall.sh

Normally managed by `pcs-wireguard-firewall.service`:

```bash
sudo /usr/local/sbin/pcs-wireguard-firewall --apply
sudo /usr/local/sbin/pcs-wireguard-firewall --check
sudo /usr/local/sbin/pcs-wireguard-firewall --clear
sudo /usr/local/sbin/pcs-wireguard-firewall --refresh-networkmanager
sudo /usr/local/sbin/pcs-wireguard-firewall --validate-config
```

It blocks new PCS-LAN traffic into the tunnel, accepts routed management only
from explicit `/32` sources, blocks WireGuard forwarding toward other PCS
interfaces, and protects administrative TCP listeners from uplink ingress. A
NetworkManager dispatcher refreshes only the narrow allow rules that its shared
`eth0` firewall table would otherwise erase. If a DDNS endpoint could not
resolve during an offline boot, the dispatcher passively retries the already
enabled tunnel after NetworkManager reports an operator-selected uplink; it
never starts Wi-Fi or cellular itself. See
[WireGuard Remote Management](../docs/wireguard-remote-management.md).

### pcs-wireguard-endpoint-refresh.sh

Normally managed by `pcs-wireguard-endpoint-refresh.service` and its five-minute
timer. It resolves the configured DDNS hostname over IPv4 and updates only the
active WireGuard peer endpoint. It does not change routes, DNS policy, or uplink
state. NetworkManager also requests a refresh after relevant uplink events.

## Meshtastic USB/Bluetooth / MQTT

## PCS APRS Agent

### setup-pcs-aprs-agent.sh

Installs or checks the read-only APRS message agent after the managed Dire Wolf
profile has a matching Internet-only `ICHANNEL`:

```bash
./scripts/setup-pcs-aprs-agent.sh --check
./scripts/setup-pcs-aprs-agent.sh --install
```

The installer never edits or restarts Dire Wolf and refuses installation when
the live KISS/ICHANNEL mapping is absent. See
[PCS APRS Agent](../docs/aprs-agent.md).

### pcs_aprs_agent.py

Connects to local Dire Wolf KISS, accepts numbered messages addressed exactly
to the PCS callsign, ACKs duplicates without re-running commands, and answers
only the documented read-only status commands. It never connects to APRS-IS or
uses the RF KISS channel.

### setup-meshtastic-bluetooth.sh

Stages or configures a persistent USB serial or BLE connection to a dedicated
Meshtastic node and transparently relays its MQTT client-proxy traffic:

```bash
./scripts/setup-meshtastic-bluetooth.sh --prepare
./scripts/setup-meshtastic-bluetooth.sh --refresh
./scripts/setup-meshtastic-bluetooth.sh --scan
./scripts/setup-meshtastic-bluetooth.sh --configure DEVICE MQTT_HOST MQTT_PORT
./scripts/setup-meshtastic-bluetooth.sh --configure-usb /dev/ttyACM0 MQTT_HOST MQTT_PORT
./scripts/setup-meshtastic-bluetooth.sh --enable-gpsd-position
./scripts/setup-meshtastic-bluetooth.sh --disable-gpsd-position
./scripts/setup-meshtastic-bluetooth.sh --enable-neomesh-map
./scripts/setup-meshtastic-bluetooth.sh --disable-neomesh-map
./scripts/setup-meshtastic-bluetooth.sh --check
```

Staging never contacts or configures the radio. Configured operation keeps the
selected transport connected continuously. USB configuration disables the
node's Bluetooth radio and exposes only `/dev/ttyACM0` inside the hardened
service. MQTT downlink starts with no subscriptions and must be given exact
topic filters deliberately. The optional GPSD feed sends a fresh local fix
every 30 minutes without retaining coordinates; this direct PCS packet cadence
is independent of the radio firmware's normal Position Broadcast Interval. The optional NeoMesh page
integration preserves the primary broker and mirrors uplink only to the
separate broker consumed by its embedded MQTT coverage map; it never subscribes
the radio to that public broker. The installed status command reads the running
gateway snapshot without reopening the radio. The public and
authenticated dashboards expose only approved aggregate node, MQTT, mesh,
GPSD, utilization, power, and environment fields. See
[Meshtastic USB/Bluetooth MQTT Gateway](../docs/meshtastic-bluetooth-gateway.md).
`--refresh` reinstalls versioned gateway assets while preserving configured or
staged state, and restarts an active gateway only when a service-relevant file
changed.

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

### xpt2046_touch_test.py

Provides a temporary, guarded raw-touch check for an uncommissioned XPT2046
resistive touch controller. The default preflight does not open SPI or change
the Pi:

```bash
python3 scripts/xpt2046_touch_test.py preflight
```

`XPT2046` identifies the touch controller only. After the photographed board was
matched to the MPI3501/tft35a family, this helper also gained a guarded
`pattern` command that writes RGB565 color bars and a grid to an already-created
16-bit framebuffer. Its `touch-map` command plots kernel ADS7846 input reports
as red dots using the vendor's 90-degree calibration bounds. It does not load a
driver or edit boot configuration.

The full temporary swap and restore procedure is in the
[Testing Checklist](../docs/testing-checklist.md#temporary-xpt2046-touch-controller-test).
Only after the PCS LCD, matrix, and any overlapping fan/backlight hardware are
physically disconnected and all listed PCS services are inactive, sample the
center and four corners:

```bash
python3 scripts/xpt2046_touch_test.py sample --seconds 20 \
  --hardware --apply --confirm-pcs-displays-disconnected
```

Observed raw coordinates establish only that the touch controller responds.
They do not validate calibration, LCD output, electrical compatibility, or a
permanent PCS GPIO allocation.

### test-xpt2046-display.sh

Provides the explicit temporary lifecycle around the framebuffer and touch
helpers. `prepare` records and suspends the related startup, LCD, WS2812, matrix,
fan, and shared GPIO-shutdown units so dependencies cannot reactivate shared
GPIO during a test. `test` loads the kernel's generic ILI9486 fbtft
overlay dynamically and shows a pattern. `touch-test` also loads the generic
ADS7846 driver on CE1/GPIO17 and plots reported contacts against five targets.
Both remove every temporary overlay with a trap. `restore` requires confirmation
that normal PCS hardware is reconnected, then restores and verifies the exact
saved service states.

```bash
sudo bash scripts/test-xpt2046-display.sh status
sudo bash scripts/test-xpt2046-display.sh test --seconds 60 --apply \
  --confirm-pcs-displays-disconnected
sudo bash scripts/test-xpt2046-display.sh touch-test --seconds 60 --apply \
  --confirm-pcs-displays-disconnected
```

It creates no service, boot hook, or persistent display overlay. See the
[Testing Checklist](../docs/testing-checklist.md#temporary-xpt2046-touch-controller-test)
for the power-off swap and restoration sequence.

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

The LCD, WS2812, and matrix installers also install and enable the shared
`pcs-gpio-startup.service`. At boot it writes `PCS Booting Up` / `Stand by...`
to the LCD, continuously cycles all six WS2812 pixels through a slower dim color
spectrum with a 0.35-second per-color dwell until handoff, and runs an
all-pixels/checkerboard MAX7219 self-test.
It waits up to 90 seconds for the normal indicator health snapshot to become
alert-free. Healthy systems hand off early. A timeout always hands off to the
normal daemons so a persistent warning or fault is not hidden.

The same installers install and arm the shared
`pcs-gpio-shutdown.service`. During a normal halt, reboot, or poweroff, the
regular display daemons stop first. The shutdown service then leaves
`PCS Offline` / `Shutting Down` on the LCD, turns all six status pixels blue,
and latches a dim bed/ZZZ icon on the matrix. Per-device marker files under
`/etc/pcs/gpio-shutdown` prevent optional hardware that was never installed
from being probed. The final images remain visible only while display power is
still present.

### pcs-gpio-startup.sh

Orchestrates the registered boot indicators and bounded readiness grace for
`pcs-gpio-startup.service`. Registration uses the same per-device marker files
as shutdown, so optional hardware is never probed merely because the service is
installed. The installed service uses the 90-second default timeout and always
stops its background WS2812 animation before releasing the normal daemons.

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

The base installer records `PCS_APRS_ENGINE` as `direwolf` or `graywolf` and
dispatches software staging to the matching script. Only Dire Wolf currently
has a supported PCS activation workflow.

### setup-graywolf-aprs.sh

Installs the pinned, checksum-verified Graywolf package without replacing an
active Dire Wolf engine:

```bash
./scripts/setup-graywolf-aprs.sh --prepare
./scripts/setup-graywolf-aprs.sh --check
./scripts/setup-graywolf-aprs.sh --capabilities
./scripts/setup-graywolf-aprs.sh --help
```

The script preserves an active Dire Wolf service, refuses an active Graywolf
service, overrides Graywolf away from PCS-reserved TCP 8080, uses tmpfs for
packet history, records the staged engine, and does not start a radio path. A
separate migration helper, `pcs-graywolf-profile.py`, provisions the matching
PCS profile with every transmitter-producing feature disabled and reads the
safety-critical values back; it is not an activation command. See
[Graywolf APRS Staging](../docs/graywolf-aprs.md).

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
./scripts/setup-direwolf-aprs.sh --set-rx-level 69
./scripts/setup-direwolf-aprs.sh --set-tx-timing 75 20
./scripts/setup-direwolf-aprs.sh --check
./scripts/setup-direwolf-aprs.sh --capabilities
./scripts/setup-direwolf-aprs.sh --software-test
./scripts/setup-direwolf-aprs.sh --install-uplink-recovery
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
- `--set-rx-level PERCENT` persists and applies a validated RX mixer level
  without regenerating Dire Wolf or touching the active TX profile.
- `--set-tx-timing DELAY TAIL` requires active Dire Wolf and records validated
  10 ms timing units only when they match `/etc/direwolf.conf`; it never
  restarts Dire Wolf.
- `--software-test` uses temporary WAV files to verify AX.25, FX.25, and timing tolerance without RF.
- `--install-uplink-recovery` installs only the guarded NetworkManager-triggered
  APRS-IS recovery helper. It records the current default interface but does not
  restart Dire Wolf or touch the radio during installation.
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

The uplink recovery helper waits for Dire Wolf's native reconnect first. It
restarts only after the default IPv4 interface changed, APRS-IS DNS resolves,
no Dire Wolf APRS-IS TCP session exists after the grace period, and the
five-minute restart cooldown has expired.

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
verifies Sabrent/C-Media card `Device` at -16 dB playback, 69% capture, and AGC
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

The script and retained NetworkManager profile name still reference the older
router-WAN design for upgrade compatibility. Its prompts and current behavior
describe the attached OpenWrt device as a bridge/AP/switch connected through a
LAN port.

### setup-cellular-profile.sh

Creates or updates the T-Mobile cellular profile and installs the PCS fallback
controller:

```text
pcs-cellular-profile
```

The profile uses APN `fast.t-mobile.com`, route metric `900`, and autoconnect
disabled in every mode. Fresh installs default to manual control from the PCS
Control Panel. If the installer selects `wifi-fallback`, the managed
`pcs-cellular-fallback.service` starts cellular after 30 seconds without active
Wi-Fi and releases only its own cellular session after Wi-Fi is stable for 30
seconds.

Set and persist the policy without rerunning the base installer:

```bash
./scripts/setup-cellular-profile.sh --fallback wifi-fallback
./scripts/setup-cellular-profile.sh --fallback manual
./scripts/setup-cellular-profile.sh --check
```

Fresh installs default to `pcs-cellular-profile`. Override the name, APN, or
route metric in `config/pcs-install.conf` with `PCS_CELLULAR_PROFILE`,
`PCS_CELLULAR_APN`, and `PCS_CELLULAR_ROUTE_METRIC`. Select `manual` or
`wifi-fallback` with `PCS_CELLULAR_FALLBACK_MODE`. Older installs that still
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

The share is restricted to the dedicated `pcs-admin` Samba account. Its
password is synchronized with the PCS web-admin password while the existing
`PCS-Share` account remains unchanged. Upgrades perform a one-time interactive
sync before changing the live share ACL.

### setup-pcs-share-discovery.sh

Installs the repeatable LAN-only discovery layer. Windows WSD advertises the
clickable name **PCS-FILE-SHARE**, Avahi advertises **PCS File Share**, and Samba
uses the same valid `PCS-FILE-SHARE` host alias. The managed service uses one
WSD/LLMNR process plus a dedicated nftables guard that exposes TCP/UDP 3702 and
5355 only through `eth0` and `wlan0`. The vendor all-interface
`wsdd2.service` stays masked. The installer validates Samba, the firewall, and
active services and retains a rollback snapshot.

```bash
./scripts/setup-pcs-share-discovery.sh
```

`pcs-wsdd.sh`, `pcs-wsdd-firewall.sh`, and `pcs-wsdd.service` are installed
automatically by this step.

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

### setup-pcs-backup.sh

Installs the root-owned automatic-backup policy helper, fixed timer/service,
and due-check runner. New installs default to enabled every 10 minutes; existing
`/etc/pcs-backup/config.json` is preserved. Version 1 hourly policies are
migrated to the equivalent version 2 minute interval. The timer checks every minute
and runs the additive backup action only when due. Web and Android settings are
validated to 1-43,200 whole minutes and can retain every dated snapshot.
Disabling automation leaves every manual sync
button available.

### sync-pcs-share-to-backup.sh

Mirrors the USB primary share to the SD-card backup share.

```bash
./scripts/sync-pcs-share-to-backup.sh
```

Sync direction:

```text
/mnt/pcs-usb/PCS-Share -> /srv/pcs-share-backup
```

Sync is additive. Files deleted from the USB primary share remain on the SD
backup. Optional dated snapshots are never automatically pruned.

## PCS Control Panel

`pcs_api_token.py` issues and revokes Stats API bearer tokens. It
also performs the fixed password-verifying `pair-from-stdin` operation used by
the HTTPS pairing endpoint. It prints a new token once and stores only its
SHA-256 digest. The deployed store and raw-token handling remain outside Git
with restricted permissions.

`setup-pcs-stats-api.sh` provides the default-disabled runtime workflow used
for the supervised PCS canary:

- `--prepare` installs inactive components and exact collector/pairing/action sudoers scopes; it requires the root-owned password helper from `setup-pcs-control-panel.sh`
- `--validate-policy FILE` validates fixed paths and explicit interface/source networks
- `--import-policy FILE` imports deployment-local policy without starting services
- `--validate-tls CERT KEY [POLICY]` checks expiry, key matching, and configured SAN identities
- `--import-tls CERT KEY` imports the certificate and restricted private key
- `--issue-token TOKEN_ID` prints a new read-only token once and stores only its digest
- `--revoke-token TOKEN_ID` disables one issued token
- `--activate` applies the source firewall first, starts TLS, checks, then enables boot
- `--check` verifies policy, TLS, permissions, services, firewall, and public redaction
- `--deactivate` disables API and firewall while preserving data
- `--rollback` removes runtime integration while preserving policy, TLS, and token data
- `--help` prints the command reference

Preparation installs the recovery-capable command as
`/usr/local/sbin/pcs-stats-api-setup`; it remains available independently of
the staging directory for checks, deactivation, and rollback.

### pcs-api-smoke-test.py

Runs safe, non-mutating checks from a PCS client or maintenance workstation.
It validates the HTTPS origin, v1 discovery document, public redaction, and
unauthenticated write protection. An optional admin token can be supplied only
through an environment variable to add authenticated status and action-catalog
checks; the token is never printed.

```bash
python3 scripts/pcs-api-smoke-test.py \
  --base-url https://192.168.50.236:9443 \
  --ca-cert /path/to/pcs-api-ca-or-certificate.pem

PCS_API_SMOKE_TOKEN='pcs_ro_...' \
python3 scripts/pcs-api-smoke-test.py \
  --base-url https://192.168.50.236:9443 \
  --ca-cert /path/to/pcs-api-ca-or-certificate.pem \
  --token-env PCS_API_SMOKE_TOKEN
```

The utility refuses HTTP, URL credentials, URL paths, and untrusted
certificates. It never requests an action challenge, changes a password, or
uses `--insecure` behavior.

None of these API commands is called by `setup-pcs-base.sh` yet. The
administrative action/password expansion was deployed under a guarded rollback
boundary on 2026-08-27. Its operator-approved full-scope pairing and
non-mutating authenticated administrative acceptance passed the same day,
including revocation and subsequent `401` denial; repository source still
requires review and release integration.

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
Meshtastic integration adds only fixed privacy-safe status and confirmed
gateway-restart actions; it does not expose radio or MQTT configuration writes.

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
- Meshtastic gateway, MQTT, GPSD position, and public-map policy state
- GPIO display, indicator, matrix, and fan state when installed
- Client access info

The script describes the commissioned OpenWrt AP/switch topology while retaining
legacy NetworkManager profile names for upgrade compatibility. It also checks
the system `i2cdetect` path directly when an unprivileged shell omits
`/usr/sbin` from `PATH`.

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
