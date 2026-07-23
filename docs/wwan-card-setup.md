# WWAN Card Setup Notes

Known-good Sierra Wireless WWAN card settings for PCS.

This document covers the known-good baseline for the Sierra Wireless EM7565 and related WWAN cards used with PCS. It is not meant to be a universal cellular modem guide. It records the settings that have actually worked for this project.

## Current PCS WWAN design

PCS expects the WWAN card to provide:

- Cellular internet over MBIM.
- A Linux network interface, normally `wwan0`.
- A ModemManager device, normally visible as `/org/freedesktop/ModemManager1/Modem/0`.
- A GNSS/NMEA serial port, normally `/dev/ttyUSB1`.
- NMEA output into `gpsd`.
- GPS time/fix data into Chrony.
- Status data for the PCS dashboard.

Normal PCS path:

```text
WWAN card -> USB adapter -> Raspberry Pi
Cellular: cdc-wdm0 / wwan0 -> NetworkManager
GNSS:     /dev/ttyUSB1 -> gpsd -> Chrony -> PCS dashboard
```

## Current verified PCS setup

Fresh reinstall verification passed on Raspberry Pi OS 64-bit Desktop / Debian 13 Trixie with:

```text
Hostname: pcs-pi
LAN subnet: 10.42.0.0/24
Pi LAN IP: 10.42.0.1
OpenWrt AP/router IP: 10.42.0.2
WWAN interface: wwan0
WWAN profile: pcs-cellular-profile
WWAN APN: fast.t-mobile.com
GPS NMEA port: /dev/ttyUSB1
gpsd: active
chrony: active
```

Known verified service path:

```text
/dev/ttyUSB1 -> gpsd -n /dev/ttyUSB1 -> Chrony SHM refclock -> PCS dashboard
```

Fresh reinstall test result:

```text
Self-test: 68 pass / 1 warn / 0 fail
Cellular: connected
GPS/GNSS: valid NMEA fix
Chrony GPS source: present
Chrony GPS reach: 377
PCS dashboard: GPS ok
```

## Known-good Linux device layout

Expected on the Raspberry Pi:

```text
/dev/cdc-wdm0      MBIM control device
wwan0             cellular network interface
/dev/ttyUSB1      GNSS NMEA serial stream
```

Other serial ports may appear. Their numbering can vary by firmware, USB composition, kernel, and host OS.

On the current EM7565 PCS build, ModemManager reports:

```text
cdc-wdm0 (mbim)
ttyUSB0  (ignored)
ttyUSB1  (gps)
```

The AT command port is easiest to access from Windows using the Sierra Wireless AT Command Port. On Linux, ModemManager may block `mmcli --command` unless debug AT passthrough is allowed, so persistent AT configuration is often easier from Windows.

## EM7565 known-good baseline

### Hardware

Current known-good hardware:

```text
Card: Sierra Wireless EM7565
USB VID:PID: 1199:9091
USB adapter: M.2 WWAN to USB enclosure/adapter
Antennas: Main LTE, diversity LTE, external active GNSS antenna
SIM/APN: T-Mobile / fast.t-mobile.com
```

Important physical notes:

- The GNSS pigtail must be good and fully seated.
- A bad or poorly seated MHF4-to-SMA GNSS pigtail can produce confusing symptoms.
- With `AT+WANT=1`, the GPS SMA should show active antenna bias, approximately 3.1–3.3 V.
- Keep RF pigtails and antennas away from noisy switching supplies where possible.
- Do not intentionally disable LTE diversity for normal PCS use.

### Firmware

Known-good EM7565 firmware states observed during PCS testing:

```text
Current carrier firmware:
SWI9X50C_01.14.02.00_TMO_002.003_003

Previously working generic firmware:
SWI9X50C_01.16.08.00_GENERIC_002.075_000
```

The current reinstall-verified card is using:

```text
Revision: SWI9X50C_01.14.02.00
Carrier/profile: T-Mobile
```

Linux `qmi-firmware-update` timed out during testing. The Windows Sierra firmware/FDT flasher successfully completed the firmware change.

### USB composition

Known-good target:

```text
DIAG + NMEA + MODEM + MBIM
Interface bitmask: 0000100D
```

Query:

```text
AT!ENTERCND="A710"
AT!USBCOMP?
```

Expected style of result:

```text
Config Index: 1
Config Type: 1
Interface bitmask: 0000100D (diag,nmea,modem,mbim)
```

Set only when needed:

```text
AT!ENTERCND="A710"
AT!USBCOMP=1,1,100D
AT!RESET
```

After reset, Linux should expose MBIM and the NMEA serial port.

### Core EM7565 AT baseline

Run from the Sierra Wireless AT Command Port, usually on Windows.

```text
ATE1
ATI
AT!ENTERCND="A710"
AT!USBCOMP?
AT!CUSTOM?
AT+WANT?
AT!GPSNMEACONFIG?
AT!GPSNMEASENTENCE?
```

Known-good GPS/GNSS settings:

```text
AT!ENTERCND="A710"
AT+WANT=1
AT!GPSNMEACONFIG=1,1
AT!GPSNMEASENTENCE=00CF
AT!RESET
```

Expected after reset:

```text
AT+WANT?
+WANT: 1

AT!GPSNMEACONFIG?
Enabled: 1
Output Rate: 1

AT!GPSNMEASENTENCE?
!GPSNMEASENTENCE: 0xCF
```

## Why `AT!GPSNMEASENTENCE=00CF`

The PCS EM7565 originally had this value:

```text
AT!GPSNMEASENTENCE: 0x79FDF
```

That produced `$GA...` Galileo-style sentences such as:

```text
$GAGGA
$GARMC
$GAGSA
```

Those sentences contradicted the valid GPS fix data by reporting fix-like position data with zero satellites or no fix. The result was gpsd/cgps flapping between valid fix and no-fix states.

The known-good PCS mask is:

```text
AT!GPSNMEASENTENCE=00CF
```

This keeps the useful standard GPS/GLONASS/GNSS NMEA stream:

```text
GPGGA   GPS fix data
GPRMC   GPS recommended minimum position/time data
GPGSV   GPS satellites in view
GPGSA   GPS overall satellite/fix/DOP data
GLGSV   GLONASS satellites in view
GNGSA   GNSS overall satellite/fix/DOP data
```

And it removes the problematic `$GA...` group from the PCS stream.

After applying `00CF`, `cgps`, `gpsd`, Chrony, and the PCS dashboard all behaved correctly.

## Expected raw NMEA after fix

Check on the Pi:

```bash
timeout 20 gpspipe -r 2>/dev/null | grep -E '^\$(GP|GN|GL|GA)(GGA|RMC|GSA|GSV)' | head -n 60
```

Expected sentence families:

```text
$GPGGA
$GPRMC
$GPGSA
$GPGSV
$GLGSV
$GNGSA
```

Not expected:

```text
$GAGGA
$GARMC
$GAGSA
```

## Expected gpsd JSON after fix

```bash
timeout 20 gpspipe -w 2>/dev/null | grep -E '"class":"(TPV|SKY)"' | head -n 20
```

Good signs:

```text
TPV mode 2 or mode 3
SKY nSat > 0
SKY uSat > 0
lat/lon present
```

## Expected PCS dashboard state

```bash
sudo /usr/local/sbin/pcs-web-action dashboard-json | jq '.cards[] | select(.id=="gps")'
```

Expected:

```text
status: ok
Fix quality: 2D fix or 3D fix
Satellites: <n> in view / <n> used
Chrony GPS source: present
```

## WWAN modem / EM74xx known-good baseline

PCS uses a generic WWAN GPS service path for modem NMEA, gpsd, and Chrony.

### Hardware

Known-good assumptions:

```text
Card: Sierra Wireless WWAN modem
Mode: MBIM
GNSS NMEA: /dev/ttyUSB1, depending on composition/host
GPS antenna: external active GNSS antenna preferred
```

The WWAN modem uses:

```text
W_DISABLE1#  WWAN/radio disable
W_DISABLE2#  GNSS disable
```

For normal PCS use, these should be left unasserted/high so WWAN and GNSS are enabled.

### USB composition

Known-good target is also:

```text
DIAG + NMEA + MODEM + MBIM
Interface bitmask: 0000100D
```

Query:

```text
AT!ENTERCND="A710"
AT!USBCOMP?
```

Set only when needed:

```text
AT!ENTERCND="A710"
AT!USBCOMP=1,1,100D
AT!RESET
```

On EM74xx-style cards, config type `1` is the normal generic USB composition type.

### Core WWAN modem AT baseline

```text
ATE1
ATI
AT!ENTERCND="A710"
AT!USBCOMP?
AT!CUSTOM?
AT+WANT?
AT!GPSNMEACONFIG?
AT!GPSNMEASENTENCE?
```

Known-good GPS/GNSS settings:

```text
AT!ENTERCND="A710"
AT!CUSTOM="GPSENABLE",1
AT+WANT=1
AT!GPSNMEACONFIG=1,1
AT!GPSNMEASENTENCE=00CF
AT!RESET
```

Expected after reset:

```text
AT+WANT?
+WANT: 1

AT!GPSNMEACONFIG?
Enabled: 1
Output Rate: 1

AT!GPSNMEASENTENCE?
!GPSNMEASENTENCE: 0xCF
```

If the WWAN modem is already working with a different sentence mask, do not change it just for fun. Use `00CF` as the PCS troubleshooting/known-good mask when gpsd/cgps fix reporting is unstable.

## NetworkManager cellular profile

PCS uses NetworkManager and ModemManager.

Current T-Mobile known-good profile:

```text
connection.id: pcs-cellular-profile
gsm.apn: fast.t-mobile.com
connection.autoconnect: no
ipv4.method: auto
ipv6.method: auto
ipv4.route-metric: 900
ipv6.route-metric: 900
```

Fresh installs default to `pcs-cellular-profile`. Older installs may still use
the legacy `pcs-cellular-tmobile` profile name; PCS status, self-test, and web
actions will use the legacy profile if it already exists. Override the
fresh-install name in `config/pcs-install.conf` with `PCS_CELLULAR_PROFILE`.

Manual connect:

```bash
nmcli connection up pcs-cellular-profile
```

Manual disconnect:

```bash
nmcli connection down pcs-cellular-profile
```

Status:

```bash
nmcli device status
mmcli -L
mmcli -m 0
ip addr show wwan0
ip route
```

Expected connected state:

```text
cdc-wdm0       gsm       connected    pcs-cellular-profile
wwan0          has IPv4 and/or IPv6 address
default route  via wwan0, metric 900
```

## gpsd baseline

Expected `/etc/default/gpsd`:

```text
START_DAEMON="true"
GPSD_OPTIONS="-n"
DEVICES="/dev/ttyUSB1"
USBAUTO="false"
GPSD_SOCKET="/var/run/gpsd.sock"
```

Keep GPSD on localhost. Do not add `-G` merely to share position data with another PCS node, because `-G` listens on every interface and may expose live coordinates over an active uplink.

For a trusted LAN client such as the Pi-Star hotspot, install the LAN-only proxy:

```bash
bash ./scripts/setup-gpsd-lan-proxy.sh
```

The proxy listens on `10.42.0.1:2947` and forwards to `127.0.0.1:2947`.
Native GPSD clients can connect directly, and `gpspipe` can provide NMEA to
applications that require sentences instead of GPSD JSON. See
[GPS Network Sharing](gps-network-sharing.md).

On Pi-Star, configure YSFGateway's native GPSD client:

```ini
[GPSD]
Enable=1
Address=10.42.0.1
Port=2947
```

Service checks:

```bash
systemctl status gpsd gpsd.socket --no-pager
gpspipe -r
gpspipe -w
cgps -s
```

PCS GPS starter service:

```bash
systemctl status pcs-wwan-gps-nmea.service --no-pager
journalctl -u pcs-wwan-gps-nmea.service -n 80 --no-pager
```

Expected starter log:

```text
ModemManager modem detected.
Sent GPS_START to /dev/ttyUSB1 at 115200.
NMEA output detected on /dev/ttyUSB1.
PCS WWAN modem GPS NMEA starter complete.
```

## Chrony baseline

PCS uses Chrony for LAN NTP and GNSS-assisted time.

Checks:

```bash
chronyc tracking
chronyc sources -v
```

Good signs:

```text
Chrony service active
System clock synchronized
GPS source present
GPS reach nonzero
```

A GPS source with reach `377` is excellent.

## Full PCS WWAN/GNSS verification checklist

Run after fresh install or modem swap:

```bash
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh

nmcli device status
mmcli -L
ip addr show wwan0
ip route

timeout 20 gpspipe -r 2>/dev/null | grep -E '^\$(GP|GN|GL|GA)(GGA|RMC|GSA|GSV)' | head -n 60
timeout 20 gpspipe -w 2>/dev/null | grep -E '"class":"(TPV|SKY)"' | head -n 20

chronyc sources -v

sudo /usr/local/sbin/pcs-web-action dashboard-json | jq '.cards[] | select(.id=="cellular"), .cards[] | select(.id=="gps"), .cards[] | select(.id=="time")'
```

Expected high-level result:

```text
Cellular: ok
GPS/GNSS: ok
Time/Chrony: ok
Self-test: pass, no hard failures
```

## Troubleshooting notes

### Cellular works but GPS has no fix

Check:

```text
AT+WANT?
AT!GPSNMEASENTENCE?
AT!GPSNMEACONFIG?
```

Known-good:

```text
+WANT: 1
!GPSNMEASENTENCE: 0xCF
Enabled: 1
Output Rate: 1
```

Also physically check:

- GNSS antenna connected to the GNSS/GPS port, not LTE aux.
- MHF4 connector fully seated.
- SMA pigtail continuity.
- Active GPS antenna bias present at SMA.

### gpsd sees satellites but cgps says no fix

Check raw NMEA for `$GA...` sentences:

```bash
timeout 20 gpspipe -r 2>/dev/null | grep -E '^\$GA'
```

If `$GAGGA`, `$GARMC`, or `$GAGSA` appear and cgps is flapping, apply:

```text
AT!ENTERCND="A710"
AT!GPSNMEASENTENCE=00CF
AT!RESET
```

Then restart PCS GPS services on the Pi:

```bash
sudo systemctl restart ModemManager
sleep 10
sudo systemctl restart pcs-wwan-gps-nmea.service
sleep 3
sudo systemctl restart gpsd.socket gpsd
sudo systemctl restart pcs-control-panel.service
```

### mmcli AT commands are rejected

This is normal on the Pi unless ModemManager debug AT passthrough is enabled.

Example failure:

```text
Unauthorized: Operation only allowed in debug mode
```

Use the Windows Sierra Wireless AT Command Port instead, or stop ModemManager and talk to the correct AT serial port directly.

### qmi-firmware-update times out

During PCS testing, Linux `qmi-firmware-update` timed out on the EM7565. The Windows Sierra firmware/FDT updater successfully flashed the modem.

Keep both known-good firmware ZIPs on the PCS share when possible:

```text
SWI9X50C_01.14.02.00_TMO_002.003_003.zip
SWI9X50C_01.16.08.00_GENERIC_002.075_000.zip
```

## Do not casually change these

Avoid changing these on a working PCS modem unless troubleshooting:

```text
AT!USBCOMP
AT!IMPREF
AT!BAND
AT!CUSTOM
Firmware carrier image
```

For PCS release builds, prefer documenting the modem baseline and leaving modem reconfiguration as a manual, deliberate step.

## Quick Windows AT command reference

Use the Sierra Wireless AT Command Port in Device Manager.

Serial settings:

```text
115200 baud
8 data bits
No parity
1 stop bit
No flow control
```

Basic identity and unlock:

```text
ATE1
ATI
AT!ENTERCND="A710"
```

Known-good EM7565 GNSS setup:

```text
AT+WANT=1
AT!GPSNMEACONFIG=1,1
AT!GPSNMEASENTENCE=00CF
AT!RESET
```

Known-good WWAN modem GNSS setup:

```text
AT!CUSTOM="GPSENABLE",1
AT+WANT=1
AT!GPSNMEACONFIG=1,1
AT!GPSNMEASENTENCE=00CF
AT!RESET
```

Known-good USB composition, only if needed:

```text
AT!USBCOMP=1,1,100D
AT!RESET
```

Verification:

```text
ATI
AT!USBCOMP?
AT+WANT?
AT!GPSNMEACONFIG?
AT!GPSNMEASENTENCE?
```

Expected GNSS baseline:

```text
+WANT: 1
Enabled: 1
Output Rate: 1
!GPSNMEASENTENCE: 0xCF
```
