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
- A temporary internet connection for package installation and, when Debian's
  Dire Wolf package is older than PCS requires, the pinned official 1.8.1 source

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
Stage Dire Wolf / APRS software:     yes
Stage Meshtastic BLE/MQTT software:  yes
Install 16x2 HD44780 LCD display:    yes (when physically fitted)
Install six-pixel WS2812 indicators: yes (when physically fitted)
Install MAX7219 LED matrix display:  yes (only when physically fitted)
Install GPIO18 hardware PWM fan:     yes (when the Armor Lite cooler is fitted)
```

The generated `config/pcs-install.conf` should therefore contain:

```bash
PCS_SETUP_WWAN_GPS=yes
PCS_SETUP_GPSD_LAN=yes
PCS_SETUP_PISTAR=yes
PCS_SETUP_APRS=staged
PCS_SETUP_MESHTASTIC=staged
PCS_SETUP_GPIO_LCD=yes
PCS_SETUP_GPIO_LEDS=yes
PCS_SETUP_GPIO_STATS=yes
PCS_SETUP_GPIO_FAN=yes
```

The GPSD setting installs a socket proxy bound only to
`10.42.0.1:2947`; GPSD itself remains localhost-only.

The base RTC and Chrony steps plus the selected WWAN GPS step recreate the
GPS-first, Internet-second, RTC-holdover time hierarchy. See
[PCS Time-Source Hierarchy](time-sources.md).

`PCS_SETUP_PISTAR=yes` enables the hotspot health checks and local-access
links. Set it to `no` on builds without Pi-Star; the dashboard and self-test
then omit those optional checks without degrading overall PCS health.
In `ALL` or `DEFAULTS` input mode this answer is collected up front. After the
installer establishes the PCS LAN on `eth0`, it immediately attempts the
coordinated-shutdown pairing before continuing with the remaining setup. SSH
asks for the Pi-Star password at that point; the password is not stored.

`PCS_SETUP_APRS=staged` means Dire Wolf is installed but stopped and disabled,
with no live APRS-IS credential or enabled RF path. Selecting APRS staging also
runs the idempotent Pi UART preparation; reboot when it reports a boot-file
change. The versioned desired profile can then be installed as one complete,
evidence-reset block:

```bash
./scripts/setup-direwolf-aprs.sh --import-commissioned-profile
./scripts/setup-direwolf-aprs.sh --record-validation
./scripts/setup-direwolf-aprs.sh --validate-config tx
```

Record validation only after reconfirming the corresponding physical check.
After commissioning, preserve `/etc/direwolf.conf`,
`/etc/pcs/aprs/backups/`, and the active-mode values from the ignored install
configuration as credential-bearing/manual recovery material. The APRS-IS
passcode is intentionally absent from Git. See
[Dire Wolf / APRS Integration](direwolf-aprs.md) for `--render-config`,
`--validate-config`, guarded activation, and `--rollback`.

`PCS_SETUP_MESHTASTIC=staged` installs the pinned USB/Bluetooth MQTT client and
persistent gateway service but leaves it stopped and disabled. After the
RAK4631 and broker are selected, restore the root-only MQTT credentials. Use
`setup-meshtastic-bluetooth.sh --configure-usb /dev/ttyACM0 HOST PORT` for the
deployed wired node; this also disables the node's Bluetooth radio. Use
`--configure` only for a separately validated BLE host, and explicitly restore
any required downlink topic filters. The radio's MQTT and channel settings,
broker credentials, RF behavior, and sensor calibration remain manual state.
See [Meshtastic Bluetooth MQTT Gateway](meshtastic-bluetooth-gateway.md).

`PCS_SETUP_GPIO_LCD=yes` installs and enables the GPIO-only 16x2 HD44780
status display. Set it to `no` on builds without the LCD; self-test then treats
the display as an intentionally omitted optional feature.

`PCS_SETUP_GPIO_LEDS=yes` installs and enables the six-pixel GPIO21 WS2812
status chain. Its pinned `rpi-ws281x` dependency is isolated in
`/opt/pcs-gpio-leds`. The driver uses the PCM output path, so it is compatible
with the separate USB Dire Wolf sound adapter but cannot share PCM with an I2S
sound device. Set it to `no` on builds without the indicators; status and
self-test then treat them as an intentionally omitted optional feature.

`PCS_SETUP_GPIO_STATS=yes` enables SPI0 if necessary and installs the hardened
MAX7219 display service. Set it to `no` on builds without the matrix; status and
self-test then treat the display as an intentionally omitted optional feature.

`PCS_SETUP_GPIO_FAN=yes` disables unused onboard analogue audio, enables PWM0
on GPIO18, and installs the fail-safe thermal controller. The USB Dire Wolf
sound adapter is unaffected. The PWM overlay becomes active after the reboot
below; before that reboot, self-test reports the pending transition as a warning.

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

Connect the Pi-Star RTL8152 USB Ethernet adapter to the PCS LAN. Before the PCS
script is applied, `eth0` receives an address in the dynamic range. Wi-Fi can
remain connected during this recovery-safe first stage. Find either temporary
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
PCS_PISTAR_DISABLE_WIFI=no ~/setup-pistar-pcs.sh --apply
sudo reboot
PCS_PISTAR_DISABLE_WIFI=no ~/setup-pistar-pcs.sh --check
~/setup-pistar-pcs.sh --apply
sudo reboot
~/setup-pistar-pcs.sh --check
```

The first reboot moves `10.42.0.3` to USB Ethernet while retaining Wi-Fi for
recovery. Do not apply the final Wi-Fi-disabled profile until the intermediate
check passes over wired `eth0`. Every apply also requires 30 uninterrupted
seconds of carrier and PCS ping responses; a carrier flap stops the installer
before it remounts or edits anything. If that happens, retain Wi-Fi and check
the adapter, cable, and EA4500 LAN port before retrying stage 1.

The script is idempotent and manages only:

- hostname `pcs-hotspot`
- the marked `dhcpcd` block for `10.42.0.3/24` on RTL8152 USB `eth0`
- a managed `/boot/config.txt` overlay that disables onboard Wi-Fi only after
  the wired handoff is verified
- a managed `rc.local` guard and Pi-Star AP-service condition that keep native
  boot behavior clean when `wlan0` is absent
- gateway, DNS, and preferred NTP server `10.42.0.1`
- YSFGateway's native GPSD client at `10.42.0.1:2947`
- disabling the unused local-serial MobileGPS path

It backs up every file it changes under `/root/pcs-pistar-backups/`, restores
Pi-Star's root and boot filesystems to read-only when it found them read-only,
and leaves Wi-Fi credentials and radio settings untouched.

After Pi-Star has rebooted at `10.42.0.3`, return to PCS and pair coordinated
shutdown:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/setup-pistar-shutdown.sh --apply
```

Enter the Pi-Star password when SSH asks. The password is used only to install
a restricted shutdown key and is not saved. If Pi-Star was already configured
and reachable during the PCS base installer, this optional pairing is attempted
immediately after the PCS LAN is configured and may already be complete. If it
was not reachable then, use the standalone command above.

## Verification

The PCS Pi SD-card wipe/rebuild path was most recently verified on August 18, 2026. That validation covered the repeatable Pi-side software path and configured integrations; it did not make OpenWrt or Pi-Star flashing, credentials, appliance backups, USB identity decisions, or on-air RF checks automatic. The procedure below remains the release-standard validation because those intentional manual checkpoints still apply.

On PCS:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh
./scripts/setup-pistar-shutdown.sh --check
./scripts/setup-direwolf-aprs.sh --check
./scripts/setup-direwolf-aprs.sh --validate-config tx
./scripts/setup-direwolf-aprs.sh --software-test
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
- Dire Wolf is safely staged and its software test passes, or its active mode has completed the documented hardware/RF validation
- Meshtastic is safely staged, or its active mode has a stable BLE session, broker connection, and validated allowlisted uplink/downlink
- required radio modes pass an operator-supervised on-air test

## Remaining Manual Checkpoints

The setup is repeatable, but intentionally not credential-free or fully
unattended. These actions remain manual:

- flashing SD cards and OpenWrt firmware
- entering or restoring Wi-Fi, callsign, and radio-network credentials
- pairing/trusting the RAK4631 and restoring its MQTT broker credentials and topic allowlist
- entering the Samba password
- selecting the correct USB storage device if detection is ambiguous
- validating RF behavior on air
- comparing the RAK4631 environment sensor with a known reference before using it for thermal alarms

These checkpoints prevent secrets from entering Git and prevent an installer
from guessing hardware or radio identity.
