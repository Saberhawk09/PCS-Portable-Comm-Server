# Testing Checklist

This checklist validates the current PCS build after setup, reboot, or major changes.

## Pi-Side Quick Test

From the repository root:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/pcs-self-test.sh
```

Expected:

```text
PCS Pi-side self-test PASSED.
Fail: 0
Warn: 0
```

Cellular data may be disconnected by design. The cellular profile should still
exist after setup, with the fallback service active only when
`PCS_CELLULAR_FALLBACK_MODE=wifi-fallback`.

Then run:

```bash
./scripts/pcs-status.sh
```

Review the output for service, network, storage, modem, GPS, Chrony, and share status.

## Git State

From the repo root:

```bash
git status
```

Expected:

```text
nothing to commit, working tree clean
```

## Reboot Test

Reboot:

```bash
sudo reboot
```

After the Pi comes back:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh
```

Expected:

```text
PCS Pi-side self-test PASSED.
```

PCS should come back without manual service startup.

## Network Test From Pi

Check addresses:

```bash
ip addr
ip route
```

Expected PCS LAN address:

```text
10.42.0.1/24
```

Ping OpenWrt AP:

```bash
ping -c 4 10.42.0.2
```

Expected:

```text
10.42.0.2 replies
```

Check neighbor table:

```bash
ip neigh show dev eth0
```

Stale entries can be cleared with:

```bash
sudo ip neigh flush dev eth0
```

## Windows Client Network Test

Connect a Windows client to the PCS Wi-Fi or Ethernet.

Run:

```cmd
ipconfig
```

Expected:

```text
IPv4 Address:      10.42.0.x
Subnet Mask:       255.255.255.0
Default Gateway:   10.42.0.1
DNS Server:        10.42.0.1
```

Ping PCS:

```cmd
ping 10.42.0.1
```

Expected:

```text
Reply from 10.42.0.1
```

Ping OpenWrt AP:

```cmd
ping 10.42.0.2
```

Expected:

```text
Reply from 10.42.0.2
```

## Internet Test

Only test this when an uplink is expected to be active.

From Windows:

```cmd
ping 8.8.8.8
ping google.com
```

Expected with active uplink:

```text
8.8.8.8 replies
google.com resolves and replies
```

If cellular data is disconnected, internet tests may fail. That does not mean PCS LAN services are broken.

## Homepage / Admin Login Test

From a PCS client browser:

```text
http://10.42.0.1
```

Expected:

```text
Public Field Network Status homepage loads
Admin Login button and panel are visible
No authentication is required for public status
```

Select **Admin Login**, or open:

```text
http://10.42.0.1/admin/
```

Expected:

```text
Unauthenticated users see the Admin Login page
Correct password opens PCS Administration
Incorrect password is rejected
Change Admin Password button is visible after login
Logout returns to the public homepage and invalidates the session
```

Select **Change Admin Password** and verify:

```text
The page requires the current password, new password, and confirmation
The forgotten-password warning names setup-pcs-control-panel.sh --reset-admin-password
An incorrect current password and mismatched confirmation are rejected
A successful change returns to Admin Login and invalidates the previous session
The new password works and the previous password no longer works
```

For forgotten-password recovery, rerun from an interactive Pi terminal and confirm that the installer prompts for a replacement:

```bash
./scripts/setup-pcs-control-panel.sh --reset-admin-password
```

Legacy compatibility URL:

```text
http://10.42.0.1:8080/
```

Expected: redirects to `http://10.42.0.1/admin/`.

Cockpit:

```text
https://10.42.0.1:9090
```

Expected:

```text
Cockpit login page loads
```

## Samba Share Test

From Windows File Explorer:

```text
\\10.42.0.1\PCS-Share
```

Expected:

```text
Primary share opens
```

Backup share:

```text
\\10.42.0.1\PCS-Backup
```

Expected:

```text
Backup share opens
```

Command Prompt:

```cmd
net view \\10.42.0.1
```

Expected:

```text
PCS-Share
PCS-Backup
```

## File Write Test

On a Windows client:

1. Open `\\10.42.0.1\PCS-Share`
2. Create a test file
3. Edit the test file
4. Save it
5. Delete it

Expected:

```text
Create, edit, save, and delete all work
```

## Backup Sync Test

Create a test file in:

```text
\\10.42.0.1\PCS-Share
```

On the Pi:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/sync-pcs-share-to-backup.sh
```

Then check:

```text
\\10.42.0.1\PCS-Backup
```

Expected:

```text
The test file appears in PCS-Backup
```

## Chrony / NTP Test

On the Pi:

```bash
chronyc sources -v
chronyc tracking
```

Expected:

```text
GPS is selected when it is usable
Internet NTP is selected when GPS is unavailable
The stratum-10 local clock is used only when neither is selectable
Chrony is running and serving time in all three states
```

For the controlled, fully restored source-failover procedure, see
[PCS Time-Source Hierarchy](time-sources.md#controlled-failover-test).

From Windows:

```cmd
w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly
```

Expected:

```text
Replies from 10.42.0.1
```

## RTC Test

On the Pi:

```bash
ls /dev/rtc*
timedatectl
dmesg | grep -i rtc
systemctl status pcs-rtc-seed.service --no-pager
sudo /usr/local/sbin/pcs-rtc-seed --check
```

Expected:

```text
RTC device exists
RTC seed service completed before Chrony
RTC contains a readable, plausible UTC value
System time is sane after reboot
```

## GPSD Test

On the Pi:

```bash
systemctl status gpsd
```

If GPS tools are installed:

```bash
cgps
```

Expected when GPS is working:

```text
gpsd is running
NMEA data is received
Satellite/fix data appears when antenna has sky view
```

Raw NMEA test:

```bash
gpspipe -r -n 40
```

Expected valid-fix indicators:

```text
$GPRMC,...,A,...
$GPGGA,...,1,...
```

If NMEA appears but there is no valid fix, check the active GPS antenna path:

```text
GPS SMA bias: about 3.1-3.3 VDC
MHF4 GPS pigtail: seated, correct connector, DC-continuous
AT+WANT: enabled for GNSS antenna power
GPSSEL/RF path: matches the SMA being used
```

## Modem Detection Test

On the Pi:

```bash
mmcli -L
```

Expected:

```text
A Sierra Wireless / Semtech modem is listed
```

Inspect modem:

```bash
mmcli -m 0
```

If multiple modems or modem numbers are present, adjust the modem number.

## Cellular Policy Test

Open:

```text
http://10.42.0.1/admin/
```

In manual mode, use the PCS Control Panel to connect cellular data.

Then test:

```bash
ip route
ping -c 4 8.8.8.8
ping -c 4 google.com
```

Expected when connected:

```text
Default route through active uplink
8.8.8.8 replies
DNS resolves
```

Disconnect cellular data from the Control Panel when finished.

For automatic mode, first confirm the installed policy without changing it:

```bash
./scripts/setup-cellular-profile.sh --check
systemctl is-enabled pcs-cellular-fallback.service
systemctl is-active pcs-cellular-fallback.service
```

Perform a supervised failover test without disrupting the operator connection:
temporarily install a test runtime configuration that names a nonexistent Wi-Fi
interface and uses zero-second stability windows, restart the service, and
verify that `wwan0` connects and `/run/pcs-cellular-fallback-owned` names the PCS
profile. Restore the installer-generated configuration immediately, restart the
service, and verify that active `wlan0` causes the owned cellular session and
marker to clear. Never use a real Wi-Fi disconnect for this test over a Wi-Fi
SSH session.

## OpenWrt AP Test

From a client:

```text
http://10.42.0.2
```

Expected:

```text
OpenWrt management page loads
```

Confirm:

```text
DHCP disabled on OpenWrt
LAN IP is 10.42.0.2
Gateway/DNS point to 10.42.0.1 if configured
```

If the AP is intentionally powered down for a supervised fault test, confirm
that the web Network card and overall header show `BAD`, the LCD replaces its
normal pages with `HARD FAULT` / `ROUTER OFFLINE`, the matrix shows `X` followed
by the Wi-Fi symbol, and WS2812 pixel 4 turns red. `pcs-self-test.sh` must report
the unavailable AP as a failure. Restore AP power and confirm every indication
clears.

## Pi-Star Integration Test

These checks apply when `PCS_SETUP_PISTAR=yes` is selected. With
`PCS_SETUP_PISTAR=no`, the self-test reports the optional integration as
skipped and the dashboard omits Pi-Star-specific health and access fields.

From PCS:

```bash
ping -c 2 10.42.0.3
curl -fsS http://10.42.0.3/ >/dev/null
```

On Pi-Star, using a current copy of the repository script:

```bash
./setup-pistar-pcs.sh --check
```

Expected:

```text
Pi-Star PCS integration check passed
GPSD VERSION response received from 10.42.0.1:2947
```

The check does not print live coordinates. Immediately after boot, retry a
time-sync or GPSD warning after a minute. Configuration failures must be fixed.
See [Full-Stack Reinstall Runbook](full-stack-reinstall.md) for the complete
reimage procedure.

Verify coordinated shutdown pairing without powering off either device:

```bash
./scripts/setup-pistar-shutdown.sh --check
```

Expected:

```text
Pi-Star coordinated shutdown pairing is ready.
```

## MAX7219 LED Matrix Test

These checks apply when `PCS_SETUP_GPIO_STATS=yes` is selected. On builds
without the matrix, keep the value `no` and the self-test reports it as skipped.

```bash
./scripts/setup-gpio-stats.sh --check
systemctl is-enabled pcs-gpio-stats.service
systemctl is-active pcs-gpio-stats.service
```

Expected when installed:

```text
Driver: installed
SPI0 CE0: available
Python spidev: available
enabled
active
```

## Six-Pixel WS2812 Status Indicator Test

These checks apply when `PCS_SETUP_GPIO_LEDS=yes` is selected. The read-only
check does not write to the LEDs:

```bash
./scripts/setup-gpio-leds.sh --check
systemctl is-enabled pcs-gpio-leds.service
systemctl is-active pcs-gpio-leds.service
```

Expected when installed:

```text
Driver: installed
Python environment: installed
Python rpi_ws281x: available
Data output: GPIO21 / physical pin 40 / PCM
Pixel count: 6
enabled
active
```

For the first supervised hardware check, stop the daemon so it is the only PCM
owner, run one sample, then restart it:

```bash
sudo systemctl stop pcs-gpio-leds.service
sudo /opt/pcs-gpio-leds/bin/python /usr/local/sbin/pcs-gpio led-status --once --hold-seconds 10 --hardware --apply
sudo systemctl start pcs-gpio-leds.service
```

Confirm the physical pixel order against the legend in
[PCS GPIO Allocation](gpio-allocation.md#six-pixel-ws2812-status-indicators).
Do not mark the LED chain tested until all six positions and GRB colors have
been observed on the installed hardware.

## Latched Shutdown Indicator Test

When any LCD, WS2812, or matrix option is selected, verify that the shared
shutdown unit is armed and that each fitted display has a marker:

```bash
systemctl is-enabled pcs-gpio-shutdown.service
systemctl is-active pcs-gpio-shutdown.service
ls -l /etc/pcs/gpio-shutdown
/usr/local/sbin/pcs-gpio shutdown-state lcd
/usr/local/sbin/pcs-gpio shutdown-state matrix
/opt/pcs-gpio-leds/bin/python /usr/local/sbin/pcs-gpio shutdown-state leds
```

The three driver commands above are simulations and report
`"writes_performed": false`. After the next supervised normal shutdown,
confirm that the LCD reads `PCS Offline` / `Shutting Down`, all six status
pixels are blue, and the matrix shows the bed/ZZZ icon while PCS remains
powered. A full removal of power blanks the displays by design.

## Dire Wolf / APRS Safety Test

These checks apply when Dire Wolf / APRS is selected during setup. Repository
tests validate software and policy; only the operator's recorded commissioning
evidence establishes audio, PTT, deviation, receiver, digipeating, and on-air
behavior for the installed hardware.

From PCS:

```bash
./scripts/setup-direwolf-aprs.sh --check
./scripts/setup-direwolf-aprs.sh --capabilities
./scripts/setup-direwolf-aprs.sh --software-test
./scripts/setup-direwolf-aprs.sh --validate-config rx
./scripts/setup-direwolf-aprs.sh --validate-config tx
```

For a software-staged installation, confirm:

```bash
systemctl is-enabled direwolf || true
systemctl is-active direwolf || true
```

Expected staged state:

```text
Dire Wolf installation and synthetic packet tests pass
Dire Wolf service is disabled and inactive
No live APRS-IS credential, audio device, PTT, beacon, or RF path is configured
RX/TX validation reports every unresolved hardware or operator decision as a blocker
```

Do not weaken or bypass a TX blocker to make this checklist pass. An intentionally
active APRS installation instead requires the complete [Safe Activation Order](direwolf-aprs.md#safe-activation-order), including bench measurements and an operator-supervised RF test.

For the commissioned active installation, also run:

```bash
systemctl --no-pager --full status pcs-sa818.service
systemctl --no-pager --full status pcs-aprs-audio.service
systemctl --no-pager --full status pcs-aprs-kiss-firewall.service
systemctl --no-pager --full status direwolf.service
sudo /usr/local/sbin/pcs-sa818 --config /etc/pcs/aprs/sa818.ini --check
sudo -u direwolf /usr/local/sbin/pcs-aprs-audio --check
sudo /usr/local/sbin/pcs-aprs-kiss-firewall --check
sudo grep -E '^(MYCALL|PTT|TXDELAY|TXTAIL|AGWPORT|KISSPORT|IGTXVIA|IGTXLIMIT|TBEACON|DIGIPEAT)' /etc/direwolf.conf
```

Expected commissioned core values are `W8IJC-10`, `PTT GPIOD gpiochip0 6`,
`TXDELAY 70`, `TXTAIL 20`, AGW 8000, KISS 8001, direct `IGTXVIA 0`, limits
`6 10`, independent `TBEACON SENDTO=0` and `TBEACON SENDTO=IG` GPS tracker
schedules, and the one-hop WIDE1-1 fill-in rule. From a PCS-LAN client, verify
both ports are reachable; from every uplink interface, verify both are rejected.
Reconfirm RF behavior after any radio, sound-card, cable, GPIO, antenna, or
Dire Wolf change.

For `PCS_SETUP_MESHTASTIC=staged`, the self-test requires the pinned BLE/MQTT
client and gateway to be installed while `pcs-meshtastic.service` stays stopped
and disabled. For `PCS_SETUP_MESHTASTIC=yes`, it additionally requires the
root-only configuration and credential files, enabled/running service, a fresh
connected USB/BLE snapshot, a live MQTT session, privacy flags, and successful
recent GPSD position delivery when selected. The status command must read the
shared snapshot without opening the radio. The commissioned PCS has
demonstrated IJC1 public-map appearance and IJC2 RF-to-map forwarding. Repeat a
controlled opt-in RF/map test after relevant radio, channel, MQTT, or map-policy
changes. Broader RF coverage and environment-sensor accuracy remain manual
hardware checkpoints;
see [Meshtastic Bluetooth MQTT Gateway](meshtastic-bluetooth-gateway.md).

With Meshtastic active, verify the public homepage contains the
**Meshtastic / MQTT** card and `/api/public-status` contains only the approved
aggregate fields. Confirm it does not expose credential keys, subscription
topic strings, channel keys, messages, remote identities, or stored
coordinates. After authenticating, verify **View Meshtastic** works and the
**Restart Meshtastic** action presents a confirmation before submission.

For an active MQTT bridge, confirm the status snapshot reports
`radio_mqtt_enabled`, `radio_proxy_enabled`, and `radio_broker_matches` as true.
After a controlled radio packet or map report, confirm `mqtt_uplink` increments;
an MQTT connection by itself is not proof that the radio supplied a publish.
When the public-map mirror is enabled, also require
`primary_channel_position_precision`, `map_position_precision`, and
`map_position_policy_ready` to report `15`, `15`, and `true`. Confirm
`map_mqtt_uplink` increments for an opted-in local MapReport or public LongFast
packet. A public-map connection without an accepted packet is not end-to-end
evidence.

## Service Status Test

On the Pi:

```bash
systemctl status smbd
systemctl status chrony
systemctl status gpsd
systemctl status cockpit
systemctl status pcs-gpio-stats.service  # when selected
```

If PCS services are installed:

```bash
systemctl list-units | grep pcs
```

Expected:

```text
Required services are active or intentionally inactive
```

## Full Field Test

Before using PCS for an event:

- [ ] PCS boots cleanly
- [ ] Pi self-test passes
- [ ] OpenWrt AP reachable at `10.42.0.2`
- [ ] Pi-Star reachable at `10.42.0.3` when installed
- [ ] Pi-Star integration check passes
- [ ] Pi-Star coordinated shutdown readiness check passes
- [ ] Client receives `10.42.0.x` address
- [ ] Client can ping `10.42.0.1`
- [ ] Client can open the public homepage and see the Admin Login panel
- [ ] Authorized operator can log in, log out, and run an admin action
- [ ] Client can open `PCS-Share`
- [ ] Client can write to `PCS-Share`
- [ ] Backup sync works
- [ ] Client can access `PCS-Backup`
- [ ] Windows NTP test works
- [ ] GPS source appears in Chrony when antenna has sky view
- [ ] Pi-Star receives the PCS GPSD protocol response
- [ ] APRS is safely staged, or its active mode has completed the documented hardware and RF validation
- [ ] Cellular can be manually connected if needed
- [ ] Cellular can be manually disconnected
- [ ] Selected cellular policy, service state, and dashboard status agree
- [ ] System survives reboot and returns to working state

## Pass Criteria

PCS is ready for field use when:

```text
Pi self-test passes
Public homepage and authenticated administration load
Clients receive PCS LAN addresses
Samba shares work
NTP works
Backup sync works
OpenWrt AP is reachable
Pi-Star integration check passes when the hotspot is installed
APRS remains safely staged or has a documented, validated active mode
Internet works when uplink is intentionally active
```
