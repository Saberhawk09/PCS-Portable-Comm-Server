# Delivered NEO-F10N: local preparation and supervised installation

Live follow-up: [November 15 F10 cutover](gnss-f10-cutover-2026-11-15.md).
The local-preparation evidence below retains its original scope and date.

Updated 2026-11-15. **Local software preparation only. PCS is offline and the
receiver is not installed. No commands in this document have been executed on
PCS for this revision.** The handoff is configuration/bench evidence, not source
cutover authority. No EM7565 AUX work is included. Legacy remains the default
until a separately authorized NEO activation; thereafter NEO is the sole receiver.

## What the delivered evidence establishes

The operator reports NEO-F10N hardware `000A0000`, firmware
`EXT SPG L1L5 6.00 (041e8a)`, UART1 115200 8N1, and a Waveshare GPS External
Antenna (D), model 25346, active L1/L5 magnetic puck with approximately 3 m coax.
Bench acquisition in about 15–20 seconds and L1/L5/E1/E5a reception were reported;
these are not unaided cold-start, Pi integration, field or PPS accuracy results.
The manufacturer interface for F10 SPG 6.00 specifies protocol 40.00; confirm
MON-VER on the Pi before enrolling. No firmware upgrade/downgrade is planned.

The two unmodified operator exports are regression fixtures under
`tests/fixtures/gnss/`. Compare them offline, without opening any serial port:

```sh
python3 scripts/pcs-gnss.py export-report \
  --stock tests/fixtures/gnss/stock-f10-neo.ucf \
  --finished tests/fixtures/gnss/f10-neo-pcs-finished.ucf
```

Expected: stock layers `380/0/0`, finished layers `380/11/11`, 17 RAM changes,
363 unchanged RAM values, `required_ram_changes: {}`, `applied: false`.
This command is analysis only; there is no UCF replay/import-to-receiver action.
BBR/flash entries cover only those 11 keys. NAV-SAT/SIG and several diagnostic
outputs are RAM-only in this evidence; their persistence remains unproven.

GGA/GSA/GSV/RMC are retained, GLL/VTG are disabled. NAV-PVT, NAV-SAT, NAV-STATUS,
NAV-POSECEF, NAV-POSLLH, NAV-DOP, NAV-TIMEUTC, NAV-CLOCK, NAV-SIG and TIM-TP have
UART1 rate 1 in the export, not a proposed five-second NAV-SIG interval.
NAV-TIMELS remains disabled. The validator uses advancing valid NAV-PVT plus
device-scoped original GPSD TOFF; it does not require enabling NAV-TIMELS.
NAV-TIMEUTC/TIM-TP report ages and NAV-SIG count/age are diagnostic only.
TIM-TP describes the next pulse; arrival is never used as a GPIO timestamp.
GPSD raw=2 forwards these UBX frames to the existing service; no second serial
reader or periodic diagnostic poller is added. Capture live rates/bandwidth
and GPSD forwarding on the delivered firmware before acceptance. Congestion
must be resolved under review, never by silently rewriting the operator profile.

## Future operator sequence — not execution authority

1. **Software staging approval, when PCS is online.** Verify the appliance's
   actual source, private changes and service state against the candidate.
   Retain the November 12 legacy deployment backup and original EM7565 antenna.
   Unpack the reviewed source archive into a separate candidate directory and
   run from there, without replacing the commissioned checkout or running the
   base/WWAN installer:

   ```sh
   python3 scripts/pcs-gnss.py preflight
   sudo python3 scripts/pcs-gnss.py stage
   ```

   `stage` prints `active: false` and an immutable `/opt/pcs-gnss/<digest>`.
   It does not install/enable a service, change the receiver or change GPSD.
   Preflight blockers are informational at this stage, not a reason to upgrade
   packages or edit hardware configuration automatically. Existing November 12
   software is historical deployment evidence; do not rerun its fixed-commit
   deployment script for this candidate. Updating installed dashboard helpers
   and source requires a later guarded software deployment with backups and
   a fresh review of live state. The runtime activation below installs its own
   version-matched timing libraries; source CLI prioritizes its sibling library.

2. **Physical USB installation approval.** Leave PPS disconnected. Preserve Pi
   UART/APRS, EM7565 LTE MAIN/AUX and data sessions. Check USB inventory before
   and after connection; inspect exactly the new interface:

   ```sh
   lsusb
   ls -l /dev/serial/by-path /dev/serial/by-id
   udevadm info --query=property --name=/dev/ttyUSB_REPLACE
   sudo fuser -v /dev/ttyUSB_REPLACE
   ```

   An empty `fuser` result normally exits 1. Stop on any owner, ambiguous USB
   identity or ModemManager contention; do not stop ModemManager to force access.
   CH340 VID/PID alone is not positive receiver identity. Do not probe the
   existing EM7565 NMEA port or a Pi UART. Disconnect u-center/other readers.

3. **Exclusive query approval on that one port.** Use a root shell for private
   output redirection (not an unprivileged shell redirect into `/root`):

   ```sh
   sudo -i
   umask 077
   cd /ABSOLUTE/REVIEWED/CANDIDATE
   python3 scripts/pcs-gnss.py probe --device /dev/ttyUSB_REPLACE --baud auto > /root/gnss-probe.json
   ```

   Expected model NEO-F10N, protocol 40.00, bench firmware/hardware above,
   `baud: 115200`, and no required RAM changes. Auto tries 115200 then 38400 on
   this one unowned port, with read-only MON-VER/VALGET queries and host termios
   changes. A known speed can instead be selected with `--baud 115200` or
   `--baud 38400`; no alternate speed is tried in explicit mode. A return to
   38400 is recoverable without changing receiver settings: record actual baud,
   verify the effective profile and investigate persistence before acceptance.
   Wrong model/protocol, a NAK or ownership failure does not trigger a baud retry.

4. **Assign the dedicated serial name.** Copy the verified `receiver` binding
   and observed `baud` into a private copy of `config-examples/gnss-neo.json`.
   With no unique CH340 serial, the binding uses the actual Pi `ID_PATH`; this
   cannot be filled in while the receiver is uninstalled. Activation creates
   `/dev/pcs-gnss-neo` for that enrolled interface only, with per-port
   ModemManager ignore. It does not rename or reserve `/dev/ttyUSB0`/`ttyUSB1`.
   Kernel numbers can change; the dedicated symlink is PCS's stable port name.
   Re-enroll deliberately if a serial-less receiver moves to another USB socket.
   Every admission still requires actual UBX model/protocol readback.

   Preserve legacy rollback through a separate enrolled modem NMEA alias when
   USB numbering changes. Its rule must match the modem identity **and** NMEA
   interface, rather than every port sharing its serial. The existing starter
   accepts a root-managed `PCS_WWAN_GPS_NMEA_PORT` service environment override;
   without it, historical installations retain `/dev/ttyUSB1`. GPSD and the
   starter must agree on the proven NMEA device. This does not authorize modem
   customization or a source switch.

5. **Electrical/PPS approval.** Follow Phase 1 of
   [the commissioning runbook](gnss-pps-commissioning.md): BCM26/pin37 ownership,
   3.3 V signaling, both power-up orders, no output/pull-down or SAFEBOOT loading,
   separately approved boot directives/reboot, unique kernel PPS and at least
   60 consecutive correct events. The software does not edit boot files.
   Measure serial offset/uncertainty independently and populate the private
   candidate; null calibration placeholders intentionally refuse activation.
   Review any required RAM-only differences. No baud, diagnostic-rate, L5 health,
   flash/BBR or reset write is part of initialization.

6. **Explicit source-cutover approval.** From the reviewed source candidate:

   ```sh
   python3 scripts/pcs-gnss.py plan --config /root/gnss-candidate.json \
     --gpio26-confirmed > /root/gnss-preflight.json
   python3 scripts/pcs-gnss.py activate --config /root/gnss-candidate.json \
     --receipt /root/gnss-preflight.json --gpio26-confirmed
   /usr/local/sbin/pcs-gnss status
   ```

   Require no blockers and a receipt less than ten minutes old. Retain the
   printed private rollback directory. GPSD's pre-start rechecks the enrolled
   device at the actual baud, makes only consented differential timing RAM
   changes, and writes `/run/pcs-gnss/gpsd.env` for the subsequent GPSD ExecStart.
   The temporary environment overrides the saved host default. Factory-baud
   recovery never changes the receiver's baud or causes EM7565 failover.
   Invalid/absent NEO data withdraws F10N/F10P, allowing network/local fallback.
   Chrony uses one F10N serial SOCK plus F10P kernel PPS locked to F10N; the old
   GPS SHM source is removed by the guarded transaction. Raw 2947, selected/manual
   2948 and the established trusted-LAN proxy retain their contracts. Manual
   position never authorizes time. LTE and radio settings remain untouched.

7. **Acceptance after approval.** Collect at least 15 minutes of `chronyc tracking`,
   `sources -v`, `sourcestats -v`, `selectdata -a -v`; verify an independent offset
   and no whole-second error. Exercise PPS/antenna/USB loss and recovery, approved
   cold-power-cycle persistence and both power-up orders. Verify absent-receiver
   network fallback, manual position, APRS/Meshtastic and all existing displays.
   No RF transmission is required. A green Chrony source alone is not acceptance.

## Stop / rollback

### USB time and position before PPS wiring

An explicitly authorized source cutover can use `"pps_enabled": false` in the
private enrolled configuration. It still requires measured `serial_offset` and
`serial_delay`, actual UBX identity/profile admission, a current preflight and
transaction backup. Generate the plan and activate **without**
`--gpio26-confirmed`; no physical wiring confirmation is claimed. GPSD uses the
F10 only, the legacy SHM refclock is removed, and validated F10N USB time is
selectable alongside network/holdover. The legacy helper's ExecCondition defers
modem GNSS initialization at subsequent starts. LTE remains independent.

In this state no PPS refclock or alias is created. The runtime cannot enable
F10P, including if an unwired GPIO produces stray edges, and reports PPS awaiting
commissioning. Omitting `pps_enabled` preserves the prior PPS-enabled policy and
all its GPIO confirmation requirements. Changing the active configuration to
enable PPS requires a separately reviewed transaction and physical validation;
editing the JSON alone is not a supported commissioning procedure. Keep the
recorded rollback and follow the electrical/runbook checks before connection.

The Pi 4 kernel can expose PPS as a virtual class device. Identification also
checks its uniquely bound `pps-gpio` platform driver, device-tree GPIO26,
rising-assert flags and supported BCM2835/BCM2711 controller; a PPS name alone
does not establish identity. See the
[Raspberry Pi PPS driver](https://raw.githubusercontent.com/raspberrypi/linux/rpi-6.18.y/drivers/pps/clients/pps-gpio.c).

### Restore the recorded configuration

Any identity, wiring, UTC continuity, PPS pairing or LTE regression fails the
phase. Before cutover, leave legacy software/settings unchanged. After cutover:

```sh
sudo /usr/local/sbin/pcs-gnss rollback --backup /var/backups/pcs-gnss/EXACT_RECORDED_DIRECTORY
```

This restores recorded files, ownership, enablement/running states and original
timing RAM where available. It does not reset or reconfigure the modem. The
original EM7565 antenna/NMEA stream must actually be available for GNSS rollback;
otherwise retain network/local timing. A mismatched/renumbered modem port leaves
legacy GPSD disabled until its device is proven and rebound, rather than opening
an unrelated USB port. Hardware/boot reversal and any reboot require separate
approval. Re-run the appliance self-test and check source selection, both GPSD
ports and consumers after rollback. No automatic receiver failover is enabled.

References: [u-blox F10 SPG 6.00 interface](https://docs.sparkfun.com/SparkFun_u-blox_NEO-F10N/assets/component_documentation/u-blox-F10-SPG-6.00_InterfaceDescription_UBX-23002975.pdf)
and [systemd environment-file ordering](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#EnvironmentFile=).
