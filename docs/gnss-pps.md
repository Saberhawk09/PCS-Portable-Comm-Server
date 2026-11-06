# Dedicated NEO-F10N and kernel PPS (development, uncommissioned)

Baseline: PCS v2.3.2. The optional software is prepared for a Raspberry Pi 4;
neither real NEO-F10N compatibility nor PPS accuracy has been accepted. Pulling
this repository changes no installed service or configuration. Legacy EM7565
remains the default when `/etc/pcs/gnss.json` is absent. Do not run the full base
installer to deploy this feature.

## Architecture and boundaries

NEO-F10N USB-C supplies power and connects the onboard CH340C to receiver UART1.
It is not native u-blox USB. UART defaults are 38400 8N1; delivery readback is
mandatory. The Pi UART remains reserved for SA818S. The breakout uses 3.3 V
logic. PPS is a separate connection to BCM GPIO26 (header pin 37); common
ground is through USB. Leave SAFE, RESET and EVENT unconnected. Use a compatible
active L1/L5 antenna on SMA and verify bias voltage, current, connector, cable
loss and power sequencing before connecting it. The reused external SMA must
connect exclusively to the new receiver. Do not split or change LTE MAIN.

Raw GPSD on loopback 2947 reads exactly one selected receiver. In NEO mode it
uses `-b` to prevent receiver writes and an enrolled `/dev/pcs-gnss-neo` alias.
An exclusive pre-start step polls MON-VER, verifies the supported protocol
40.00 and reads configuration. Only differences from the explicitly authorized
RAM profile are applied and read back. Original values are saved first. Neither
boot nor ordinary installation saves flash/BBR, changes L5 health policy, or resets
the receiver. Other firmware is refused pending a documented compatibility update.

The UTC validator reads receiver-specific raw UBX NAV-PVT and GPSD TOFF over
GPSD connections. It checks validDate/validTime/fullyResolved, fix acceptance,
time accuracy, UTC and GPS-week epoch continuity, increasing timestamps and
monotonic freshness. Confirmation flags are not assumed when unsupported by the
protocol. These checks are validation, **not cryptographic authentication**.
Leap-transition ambiguity is withheld rather than guessed. Recovery requires
five seconds of advancing valid epochs; freshness is two seconds. Manual
position is never read by this validator.

Validated TOFF uses the timestamp captured by GPSD, not bridge receipt time, in
a private Chrony SOCK refclock `F10N`. GPIO26 is owned by Linux `pps-gpio`;
Chrony consumes kernel timestamps as `F10P`, with `lock F10N maxlockage 2`.
PPS sysfs events are diagnostics only, never userspace GPIO timestamps.
Device identity follows the GPIO controller and device-tree line, not pps0.
Unpaired or invalid references are marked noselect; service exit and watchdog
failure withdraw them. Both start noselect until validation recovers.

Hierarchy: selectable paired PPS, selectable USB GNSS time, internet NTP, then
existing RTC-seeded local holdover. Both GNSS references are preferred, with
different measurement uncertainties; Chrony retains falseticker checks and
selects on quality. A rejected reference cannot override a valid network source.
There is no `trust`. The serial offset and uncertainty must be measured; an
example value is not calibration. The DS1307 remains a boot seed, not a running
refclock. No new clock stepping is introduced.

Selected-position GPSD on 2948, manual fallback/recovery, APRS, Meshtastic,
Pi-Star and trusted-LAN sharing retain their existing contracts. Receiver modes
are explicit; degraded timing does not select EM7565 or alter LTE sessions.
EM7565 is not concurrently attached to the raw GPSD/SHM timing path in NEO mode.

## Optional software preparation and activation

Development and staging need no hardware. All commands below target a later,
separately authorized installation on Linux. Preflight/plan are passive; `probe`
transmits UBX queries and requires an explicitly selected unowned serial port.

```bash
# Read-only; blockers are JSON, exit 1 means not ready.
python3 scripts/pcs-gnss.py preflight
# Installs an inactive versioned bundle only; no service or boot changes.
sudo python3 scripts/pcs-gnss.py stage
# Later, after USB enrollment is authorized (not a generic CH340 scan):
sudo python3 scripts/pcs-gnss.py probe --device /dev/ttyUSB_REPLACE > /root/gnss-probe.json
```

Prepare a private candidate from `config-examples/gnss-neo.json`; copy the entire
verified receiver binding and replace null serial offset/delay with measured
values. The example deliberately cannot activate. CH340 metadata alone never
proves NEO-F10N identity. Without a unique USB serial, enrollment is tied to one
physical USB path; moving it requires supervised enrollment. A replacement on
that path must still pass MON-VER. USB metadata is not a security attestation.

Install dependencies separately in the maintenance window: gpsd with UBX/TOFF/raw
support, gpsd-clients, Chrony with SOCK/PPS/selectopts, pps-tools, psmisc (`fuser`),
udev and Raspberry Pi `pinctrl`. No package upgrade is part of activation.
The initial validated baseline is GPSD 3.25 and Chrony 4.6; older versions are
refused for NEO activation until separately qualified. Legacy mode is unchanged.
Record versions and compare them with disposable integration evidence. Confirm
the existing Chrony include, network pool, LAN permissions, RTC seed and
holdover. Custom refclocks require manual review; the installer does not delete
arbitrary operator configuration.

After the hardware runbook has separately enabled and verified GPIO26 PPS:

```bash
sudo python3 scripts/pcs-gnss.py plan --config /root/gnss-candidate.json \
  --gpio26-confirmed > /root/gnss-preflight.json
sudo python3 scripts/pcs-gnss.py activate --config /root/gnss-candidate.json \
  --receipt /root/gnss-preflight.json --gpio26-confirmed
sudo /usr/local/sbin/pcs-gnss status
```

The confirmation records physical wiring/startup checks; it does not bypass
kernel identity or input/no-pull checks. A receipt expires after ten minutes or
any observed environment/configuration change. Use the repository CLI for
activation; the installed CLI supports runtime preparation, status and rollback.
Activation takes a transaction lock and private backups, then changes only the
GNSS GPSD, Chrony, enrolled-port udev rule and validation unit. No ModemManager
restart, modem AT command, USB reset, LTE change, radio command or boot edit is
included. GPSD reconnects to the same enrolled receiver; PPS disappearance
rebuilds Chrony's optional runtime include. Internet/local fallback remains
available even when the receiver is absent at a later boot.
Existing distribution AppArmor profiles receive only local rules for the PPS
include and enrolled serial alias. Original local rules are backed up/restored;
profiles are not disabled or put into complain mode. The validator has only
DAC override capability to reach Chrony's private control socket; it receives
no clock-setting or network-administration capability.

Chrony source selection and real accuracy are acceptance steps after activation,
not implied by a successfully started unit. Status separates position fix,
validated UTC, pulse activity, pairing, selected source and clock uncertainty.
Public diagnostics contain no new exact coordinates, raw reports or USB identity.
LCD, matrix and RGB retain their debounce; Manual cannot hide PPS degradation.

## Rollback

Retain the private transaction directory printed at activation, including its
receiver RAM snapshot. Run from an independent local management connection:

```bash
sudo /usr/local/sbin/pcs-gnss rollback --backup /var/backups/pcs-gnss/REPLACE
```

Rollback stops only the new validator/GPSD, attempts to restore original receiver
RAM, restores file contents/ownership/modes/symlinks and recorded service states,
then restores the original Chrony/GPSD configuration. It never starts the WWAN
initializer or issues modem commands. If receiver RAM cannot be restored because
the hardware is absent, software rollback still proceeds and reports
`receiver_ram_restored: false`; retain the snapshot for supervised restoration.
RAM-only changes also clear with a complete receiver power removal. That power
operation and boot-overlay reversal remain separate operator actions.

Before restoring legacy GPSD after USB topology changes, verify its configured
NMEA device still belongs to EM7565. Do not rerun the old WWAN installer: it
resets USB and restarts ModemManager. If the original modem stream is unavailable,
preserve internet NTP/local holdover and investigate under supervision. Confirm
2947/2948/LAN proxy, Chrony, manual fallback and PCS self-test after rollback.
If saved modem identity or interface no longer matches its configured device,
rollback leaves GPSD and its socket stopped/disabled and reports
`legacy_gpsd_restored: false`, preserving network/local timing. Rebind the proven
EM7565 NMEA port under supervision before restoring recorded GPSD enablement;
resolve this before another boot.

## Official references

- [SparkFun board hardware](https://docs.sparkfun.com/SparkFun_u-blox_NEO-F10N/hardware_overview/)
- [u-blox NEO-F10N integration manual, R04](https://content.u-blox.com/sites/default/files/documents/NEO-F10N_IntegrationManual_UBXDOC-963802114-12193.pdf), TIMEPULSE/SAFEBOOT and UTC validity.
- [u-blox F10 SPG 6.00 interface, UBX-23002975 R01](https://docs.sparkfun.com/SparkFun_u-blox_NEO-F10N/assets/component_documentation/u-blox-F10-SPG-6.00_InterfaceDescription_UBX-23002975.pdf), protocol 40.00, NAV-PVT and CFG database keys.
- [GPSD JSON protocol](https://gpsd.gitlab.io/gpsd/gpsd_json.html), TOFF, device-scoped WATCH/raw.
- [Chrony reference](https://chrony-project.org/doc/4.6/chrony.conf.html), SOCK, PPS, lock, maxlockage, selectopts and source selection.
- [Raspberry Pi PPS overlay](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/arch/arm/boot/dts/overlays/pps-gpio-overlay.dts), input and no-pull defaults; verify the installed kernel version.

The [commissioning runbook](gnss-pps-commissioning.md) contains physical acceptance,
EM7565 shared AUX/GNSS and the separately authorized L5 procedure. Historical
tests and existing hardware acceptance records are unchanged.
