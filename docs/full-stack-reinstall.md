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

First make a final additive copy of the USB share into the SD-card backup and
confirm the removable USB data is current:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/sync-pcs-share-to-backup.sh
./scripts/pcs-self-test.sh
```

Then inventory and export the credential-bearing PCS configuration to trusted
removable storage that will **not** be wiped. Do not put this archive in Git,
cloud storage, or the replacement SD-card image:

```bash
./scripts/pcs-reinstall-state.sh --check
./scripts/pcs-reinstall-state.sh --export /mnt/pcs-usb/PCS-Share/pcs-reinstall-state-YYYYMMDD.tar.gz.enc
cd /mnt/pcs-usb/PCS-Share
sudo sha256sum --check pcs-reinstall-state-YYYYMMDD.tar.gz.enc.sha256
```

The AES-256 encrypted archive includes the installed answer file, commissioned
INA226 calibration/shutdown policy, APRS and Meshtastic credentials/configuration,
WireGuard source and generated key material, Pi-Star shutdown key,
administrator/API state, SSH host identity and permanent authorized keys,
NetworkManager profiles, Samba credentials, Bluetooth bonds, and retained APRS
application state when those paths exist. It deliberately does not archive
`PCS-Share` itself, OpenWrt, Pi-Star, `/etc/shadow`, or a raw Android app token.
The Stats API stores only a hash of each app token: restoring that server state
keeps an unchanged paired phone working, but deleted Android app data must be
paired again.

Record separately:

- a known Raspberry Pi OS login password and the PCS administrator password,
  or the decision to set new ones
- the intended USB filesystem identity and current mount
- the archive filename and matching SHA-256 checksum
- the archive passphrase, retained separately from the archive
- any files that exist only on `PCS-Share`

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

Use Raspberry Pi Imager to install **Raspberry Pi OS Lite (64-bit)** for this
validation. In Imager customization, set hostname `pcs-pi`, create the normal
user named exactly `pi`, enable SSH, and configure the temporary installation
Wi-Fi. PCS services are web/systemd based and do not require a graphical
desktop. The installer fails before mutation if the account or repository path
is incompatible with its fixed service paths.

Boot, verify Internet access and system time, and clone the repository at the
required path:

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/Saberhawk09/PCS-Portable-Comm-Server.git
cd PCS-Portable-Comm-Server
```

Verify the exact release or candidate commit under test. For a pre-release wipe
test, use the exact reviewed commit supplied for the test rather than a moving
branch:

```bash
git checkout REINSTALL_TEST_COMMIT
test "$(git rev-parse HEAD)" = "REINSTALL_TEST_COMMIT"
git status --short --branch
```

Do not restore `pcs-install.conf`, `/etc/pcs`, or any credential archive before
the acceptance run. The clean-install contract is one installer command:

```bash
./scripts/setup-pcs-base.sh
```

Answer its prompts interactively. Do not run individual component setup scripts
first. A failure or skipped selected component is an installer failure, even if
the script continues to present diagnostics.

For the current GPS-sharing build, select:

```text
Configure WWAN modem NMEA GPS:       yes
Share GPSD with trusted PCS clients: yes
Include Pi-Star in PCS monitoring:   yes
Stage Dire Wolf / APRS software:     yes
Stage Meshtastic USB/Bluetooth MQTT software: yes
Install 16x2 HD44780 LCD display:    yes (when physically fitted)
Install six-pixel WS2812 indicators: yes (when physically fitted)
Install MAX7219 LED matrix display:  yes (only when physically fitted)
Install GPIO18 hardware PWM fan:     yes (when the Armor Lite cooler is fitted)
Install dual INA226 power monitoring: yes (only on the commissioned PCS hardware)
INA226 configuration profile:        commissioned-pcs (only on this unchanged PCS hardware)
Install active-low GPIO13 audible status: yes (when the pull-up/wiring is confirmed)
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
PCS_SETUP_POWER_MONITOR=yes
PCS_POWER_PROFILE=commissioned-pcs
PCS_SETUP_BUZZER=yes
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

`PCS_SETUP_APRS=staged` means the engine selected by `PCS_APRS_ENGINE` is
installed but stopped and disabled, with no live APRS-IS credential or enabled
RF path. Selecting APRS staging also runs the idempotent Pi UART preparation;
reboot when it reports a boot-file change. Graywolf staging stops there; see
[Graywolf APRS Staging](graywolf-aprs.md). For the commissioned Dire Wolf path,
the versioned desired profile can then be installed as one complete,
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

To make the RAK4631 use the PCS receiver's live GPSD fix after configuring its
gateway transport, run:

```bash
./scripts/setup-meshtastic-bluetooth.sh --enable-gpsd-position
./scripts/setup-meshtastic-bluetooth.sh --enable-neomesh-map
```

The first command enforces the commissioned 1800-second PCS GPSD position
cadence. This is independent of the radio firmware's normal Position Broadcast
Interval. The second command enforces the 15-bit primary-channel position
precision and preserves the separate hourly firmware MapReport.

The second command preserves the primary NeoMesh broker and enables only the
uplink mirror consumed by the MQTT coverage map embedded at
`neome.sh/meshtastic/`. Its public uplink credentials are installed in the
existing root-only MQTT environment file; no public-map downlink is enabled.

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

Each selected visual-display installer also installs the common bounded
`pcs-gpio-startup.service` and registers only its own hardware. It displays the
boot message and self-tests before normal status daemons begin, handing off
early when health inputs settle or after 90 seconds so persistent alerts remain
visible. The same installers arm `pcs-gpio-shutdown.service`, which restores the
LCD offline text, six blue WS2812 pixels, and bed/ZZZ matrix shutdown state
without a separate manual setup step.

`PCS_SETUP_GPIO_FAN=yes` disables unused onboard analogue audio, enables PWM0
on GPIO18, and installs the fail-safe thermal controller. The USB Dire Wolf
sound adapter is unaffected. The PWM overlay becomes active after the reboot
below; before that reboot, self-test reports the pending transition as a warning.

`PCS_SETUP_POWER_MONITOR=yes` installs the dual-INA226 monitor and persistent
diagnostic journal. Selecting `PCS_POWER_PROFILE=commissioned-pcs` makes the
one-command installer validate and install the repository's versioned `0x40`
input/`0x4c` 5V profile with the commissioned 2 milliohm shunts and guarded
shutdown policy. This choice is valid only for the same unchanged PCS hardware.
Other builds must use `generic`, complete calibration, and explicitly arm
shutdown later. An invalid profile or unavailable monitor is a reinstall
failure and must not be ignored.

`PCS_SETUP_BUZZER=yes` installs the active-low GPIO13 audible-status service.
Use it only with the confirmed external approximately 10k SIG-to-3.3V pull-up.

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
- cgroup-v2 fstab compatibility plus mode-aware D-Star and ARM `haveged`
  service guards for the tested Pi-Star image
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

The PCS Pi SD-card wipe/rebuild path was most recently verified on Raspberry Pi
OS 64-bit Desktop on August 18, 2026. This run is the first full Raspberry Pi OS
Lite 64-bit acceptance and the first wipe test of the v1.8 power/buzzer stack.
Do not update the README to call Lite validated until every acceptance item
below passes after a cold boot. OpenWrt/Pi-Star flashing, credentials, appliance
backups, USB identity decisions, and on-air RF checks remain intentionally
manual.

On PCS:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh
./scripts/setup-pistar-shutdown.sh --check
./scripts/setup-direwolf-aprs.sh --check
./scripts/setup-direwolf-aprs.sh --validate-config tx
./scripts/setup-direwolf-aprs.sh --software-test
./scripts/setup-power-audio.sh --check
sudo /usr/local/sbin/pcs-power-monitor check-config --config /etc/pcs/power-monitor.json
```

For the Lite image also record:

```bash
. /etc/os-release; printf '%s\n' "$PRETTY_NAME"
systemctl get-default
systemctl --failed --no-pager
```

No graphical target or Wayland compositor is required. Raspberry Pi Connect may
be absent and should be reported as an expected skip, not installed merely to
make a headless image resemble Desktop.

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
- the host reports Raspberry Pi OS Lite 64-bit and reaches `multi-user.target` without failed units
- power-monitor configuration validates, both commissioned INA226 addresses respond, and `pcs-power-monitor.service` is enabled and active
- the GPIO13 buzzer service is enabled/active and POST, OK, WARN, BAD, and shutdown patterns are operator-confirmed
- Pi-Star integration check has no configuration failures
- OpenWrt and Pi-Star retain `.2` and `.3`
- Pi-Star time synchronizes through PCS
- Pi-Star receives a GPSD protocol response from PCS
- coordinated shutdown readiness check passes
- Dire Wolf is safely staged and its software test passes, or its active mode has completed the documented hardware/RF validation
- Meshtastic is safely staged, or its active mode has a stable selected USB/BLE transport, broker connection, policy-safe allowlisted uplink/downlink, GPSD position health, and map policy when enabled
- required radio modes pass an operator-supervised on-air test

## Optional Post-Acceptance Credential Recovery

Only after the clean one-command installer and cold-boot acceptance pass, mount
the retained USB without formatting and verify/decrypt the recovery archive into
a root-only staging directory:

```bash
lsblk -f
sudo install -d -m 0755 /mnt/pcs-recovery
sudo mount -o ro /dev/disk/by-uuid/RECORDED_PCS_USB_UUID /mnt/pcs-recovery
cd /mnt/pcs-recovery/PCS-Share
sudo sha256sum --check pcs-reinstall-state-YYYYMMDD.tar.gz.enc.sha256
sudo install -d -o root -g root -m 0700 /root/pcs-reinstall-state
openssl enc -d -aes-256-cbc -pbkdf2 \
  -in pcs-reinstall-state-YYYYMMDD.tar.gz.enc \
  | sudo tar -xzf - -C /root/pcs-reinstall-state
```

Do not bulk-copy the staging tree over `/`. Restore only state that is still
needed, preserving its recorded ownership/mode, and rerun the owning setup
script plus self-test afterward. Generated units and helpers must always come
from the candidate repository.

- `/etc/pcs`, `/etc/wireguard`, and the ignored repository `private-config`
  carry WireGuard, APRS, Meshtastic, power, and Pi-Star shutdown material
- `/etc/pcs-stats-api` restores server token hashes and TLS state; it cannot
  recreate a lost raw Android token
- `/etc/ssh` and `/home/pi/.ssh` restore host identity and permanent authorized
  keys after confirming that no temporary access key is present
- `/var/lib/samba/private` can restore Samba credentials only when the clean
  image's Samba version is compatible; otherwise reset them through PCS setup
- `/var/lib/bluetooth` restores bonds only when deliberately retaining BLE

## Remaining Manual Checkpoints

The setup is repeatable, but intentionally not credential-free or fully
unattended. These actions remain manual:

- flashing SD cards and OpenWrt firmware
- entering or restoring Wi-Fi, callsign, and radio-network credentials
- restoring the RAK4631 identity/configuration, MQTT broker credentials, and topic allowlist; BLE pairing is needed only when BLE is deliberately selected instead of deployed USB
- entering the Samba password
- selecting the correct USB storage device if detection is ambiguous
- validating RF behavior on air
- comparing the RAK4631 environment sensor with a known reference before using it for thermal alarms

These checkpoints prevent secrets from entering Git and prevent an installer
from guessing hardware or radio identity.
