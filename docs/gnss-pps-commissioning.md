# GNSS/PPS commissioning — operator-run, hardware acceptance pending

This runbook is preparation, not execution authority. No hardware phase has been
performed. Use an independent local management connection and record time,
firmware/package versions, source commit, wiring photographs and private backups.
Separate approvals cover software deployment, GPIO/boot changes and reboot,
receiver persistent settings/L5 override, and modem customization/reset. No RF
transmissions are included. Do not repeat historical acceptance as new evidence.

## Phase 1 — USB, antenna, electrical and kernel PPS

1. Preserve the existing PCS self-test, Chrony tracking/sources/sourcestats,
   GPSD device list, raw-versus-selected location state and cellular baseline.
   Keep exact coordinates and receiver reports private. Record the existing
   boot file and overlay state without modifying them.
2. Physically trace pin 37 and audit every GPIO config/owner. Confirm BCM26 has
   no fan, display, PTT, buzzer or other connection. Confirm SA818S still owns
   the primary UART. Do not run a general GPIO output test on pin 26.
3. Attach NEO-F10N through USB only. Inventory `lsusb`, `udevadm info`, persistent
   by-path/by-id links and `fuser`. Do not assume a ttyUSB number or treat CH340
   branding as receiver identity. Obtain authorization for exclusive UBX polling
   on that specific port; run the documented `probe`. Capture MOD, firmware,
   protocol, baud/protocol outputs and time-pulse readback privately.
4. Verify the helix antenna datasheet: active L1/L5 coverage, bias voltage/current,
   SMA gender/polarity and extension attenuation. Move the former EM7565 GNSS
   bulkhead to NEO only after documenting its original path. Keep MAIN/AUX intact.
   Test sky view with USB only before connecting PPS to the Pi.
5. Observe PPS voltage, edge and pulse duration electrically where practical.
   u-blox TIMEPULSE shares startup behavior with SAFEBOOT: low loading at receiver
   startup can enter recovery mode. Inspect Pi default pulls and both power-up
   orders. No pull-down/output drive is acceptable. If a high-impedance input
   cannot be assured throughout power sequencing, stop and resolve electrical
   isolation/bias before connecting. A running-kernel input check alone is not
   proof of safe startup.
6. Under separate boot-change approval, back up the actual Pi boot configuration
   (`/boot/firmware/config.txt` or the installed OS equivalent), then prepare:

   ```ini
   gpio=26=ip,np
   dtoverlay=pps-gpio,gpiopin=26
   ```

   Inspect the installed overlay's input/no-pull and rising-assert behavior; do
   not blindly copy an overlay from a different kernel. The firmware directive
   does not prove reset-time electrical safety. No falling-edge option is used
   with the commissioned rising-edge receiver profile. Perform any required
   reboot only when specifically authorized.
7. Connect PPS to BCM26/pin37, with common USB ground. Leave SAFE/RESET/EVENT
   untouched. Verify `pinctrl get 26`, `gpioinfo`, kernel logs, `/sys/class/pps`,
   device-tree GPIO/controller identity and `ppstest` on the resolved PPS node.
   Never assume `/dev/pps0`. Check at least 60 consecutive 1 Hz assert events
   without clear-edge substitution, duplicates or unexplained gaps.

**PASS:** positive receiver identity and communication; antenna electrically
compatible; no ownership conflict or unsafe startup loading; real advancing
kernel PPS events match the measured edge. Satellite acquisition is recorded
separately. **FAIL:** stop before timing activation. Disconnect PPS safely,
restore backed-up boot configuration under approval, and retain original GPSD,
Chrony and LTE operation. Reboot reversal is a separate authorized action.

## Phase 2 — UTC/PPS pairing and timing fallback

1. Record GPSD/Chrony versions and compiled capabilities. Stage software using
   [the optional setup instructions](gnss-pps.md). Keep original configuration,
   enabled/running unit states and recovery instructions private and accessible.
2. Measure serial latency/uncertainty against an independent known-good time
   source. A coarse timestamp must identify the correct PPS second; choose
   conservative bounds including USB jitter. The candidate requires measured
   offset and delay; no hard-coded example calibration is accepted.
3. Review the RAM-only profile diff and approve initialization. It keeps NMEA,
   enables required UBX reports, uses UTC-aligned rising-edge 1 Hz pulses, and
   suppresses unlocked pulses through the documented unlocked pulse length.
   It does not enable GPS L5 overrides, write flash/BBR or reset the receiver.
4. Generate a current preflight receipt and explicitly activate. Verify exactly
   one raw receiver, no duplicate refclocks/SHM readers, selected-position 2948
   unchanged, validator/watchdog active, and source IDs F10N/F10P paired correctly.
5. Collect `chronyc tracking`, `sources -v`, `sourcestats -v` and `selectdata -a -v`
   for at least 15 minutes after stable acquisition. Record selected source,
   offsets, frequency, jitter, root delay/dispersion and independent reference
   comparison. No whole-second error is acceptable. Chrony selection or a receiver
   datasheet's nanosecond figure does not establish LAN-client absolute accuracy.
6. Under supervision, remove PPS while retaining valid serial UTC: expect USB
   GNSS time if selectable, with degraded precision status. Restore PPS and
   verify stable recovery. Then remove satellite reception: do not present pulses
   or cached UTC as valid. Confirm network NTP selection when available.
7. Separately authorize a Chrony-only network-source exclusion to observe local
   holdover; never induce WAN faults or change LTE sessions. Restore source
   eligibility in cleanup. Do not write RTC from holdover. Check same-receiver
   USB disconnect/reconnect, absent-at-boot behavior during the approved boot
   window, validator failure/watchdog withdrawal, and restoration of normal PPS.

**PASS:** valid UTC-to-PPS pairing, stable selectable PPS, characterized timing
statistics, accurately labelled USB/network/local fallback and bounded rejection
of invalid/stale input. Absolute precision target is set after measurements.
**FAIL:** execute transaction rollback; verify original GPSD/Chrony, location and
network time path. Boot configuration and electrical changes have their own
rollback. Retain data; do not claim hardware precision from simulated tests.

## Phase 3 — optional EM7565 shared AUX/GNSS

This phase is independent of NEO timing. A no-fix AUX result must not impair LTE.
Do not change MAIN, diversity/MIMO, band selection, carrier aggregation, USB
composition, APN, antenna bias or data-session configuration to obtain GNSS.

1. Record exact modem firmware/carrier revision and pre-change LTE bearer,
   registration, bands/CA state, signal, throughput and stability. Use existing
   read-only diagnostics before any separately authorized AT session.
2. Obtain the applicable [manufacturer EM75xx command reference](https://source.sierrawireless.com/resources/airprime/minicard/75xx/41111748-airprime-em75xx-at-command-reference).
   Revision 8's official download currently requires sign-in; retain the document
   matching the installed firmware. The manufacturer-authored [revision 4 copy](https://www.bipom.com/documents/GetWireless/41111748%20EM75xx%20and%20EM-MC74x1%20AT%20Command%20Reference%20r4.pdf)
   describes authentication and GPSSEL on pages 16 and 25–26, but does not state
   GPSSEL-specific activation/reset semantics. Resolve that gap through matching
   firmware documentation or manufacturer support **before writing GPSSEL**.
   If unresolved, mark this phase BLOCKED and leave the modem unchanged.
3. After explicit AT-session authorization, use a suitable owner-coordinated
   diagnostic AT port. Never contend with ModemManager's port, stop it implicitly,
   or use `mmcli --command` with credentials in process arguments. Use an
   interactive terminal with local echo, logging, scrollback capture and shell
   tracing disabled for authentication. Handle modem echo privately as well.
4. Read firmware identification (`ATI`/`AT+GMR` as documented). Authenticate with
   `AT!ENTERCND="<operator-provided key>"`; never include a real key in source,
   logs or reports. Do not use `AT!ENTERCND?`: it can disclose the stored key.
   Do not change the key with SETCND. Authentication remains unlocked until
   modem reset/power cycle; do not promise that closing the terminal relocks it.
5. Query `AT!CUSTOM=?` for supported customization names and `AT!CUSTOM?` for
   enabled customizations. Record baseline GPSSEL. Revision 4 documents default
   zero and a list of enabled settings, so a missing entry alone must not be
   presented as an explicit zero readback without firmware documentation.
   Confirm that GPSSEL=1 is supported; unsupported/ambiguous responses stop here.
6. Review the exact activation requirements and potential data-session impact.
   If a reset is required, obtain separate downtime/reset approval before any
   write. Do not guess that a GNSS restart is sufficient or that a modem reset
   is required. Once admitted, the only customization change is:

   ```text
   AT!CUSTOM="GPSSEL",1
   ```

   Perform only the documented, separately approved activation action. Read back
   the setting using the supported firmware query; record configured versus
   proven-active antenna path separately.
7. Observe GNSS for 15 minutes with sky view; record fix, satellites and time to
   acquisition without coordinates in public reports. Repeat LTE measurements
   with the same placement/test endpoint. Require maintained registration,
   existing diversity/MIMO/CA capability and no unexplained disconnects. Throughput
   varies with the network; investigate material regression before acceptance.
8. Test rollback using `AT!CUSTOM="GPSSEL",0` and the same documented activation
   process. Restore the dedicated GNSS antenna physically if reception is needed;
   software selection alone cannot restore an antenna moved to NEO. Verify LTE
   and original GNSS behavior. Return to shared mode only with operator direction.

**PASS:** firmware-supported applied setting, documented activation and tested
rollback, maintained LTE behavior. A separate **GNSS reception PASS** requires
actual fix acquisition through AUX; otherwise label AUX GNSS unavailable and keep
LTE unchanged. Neither a successful AT response nor shared-path support makes
EM7565 a reliable backup. No automatic receiver failover is enabled.

## Phase 4 — PCS ecosystem and physical acceptance

Check APRS/Graywolf/Dire Wolf and Meshtastic position consumers without initiating
RF, Pi-Star connectivity, public/admin dashboards, LCD/matrix/RGB health, raw
2947 versus selected 2948 and trusted-LAN GPSD proxy. Exercise manual fallback and
persistent Manual: coordinates may remain valid while time reports degraded.
Verify that no new public coordinates/raw reports/USB identifiers are exposed.

In an approved boot/shutdown window, check normal startup and shutdown, receiver
power sequencing, no service-order cycles, absent hardware fallback and clean
recovery. Compare NEO and EM7565 acquisition in the same conditions without
concurrently mixing their timing references. Compare antenna inside cassette,
outside on magnetic base and with the proposed extension. Record visible/used
satellites, acquisition/recovery, Chrony statistics and exact final configuration.

**PASS:** all existing consumers and manual selection retain their contracts;
timing degradation is distinct from location; ordinary startup/shutdown and
physical antenna arrangements meet recorded acceptance. **FAIL:** transaction
rollback plus phase-specific hardware reversal, then repeat PCS self-test and
confirm LTE and original position/time path. RF/PTT testing requires a separate
duration and authorization and is not required by this runbook.

## Separate GPS L5 health-policy opt-in

Read back current signal and health settings. The [SparkFun procedure](https://docs.sparkfun.com/SparkFun_u-blox_NEO-F10N/enable_l5_band_and_override_health_status/)
and u-blox integration manual describe firmware where GPS L5 needs enablement,
health override, persistent storage and GNSS restart. Do not automatically apply
old guidance to delivered firmware or assume L5 is already in use.

Prepare an exact before/after diff for the delivered firmware. If required and
specifically authorized, use documented CFG-VALGET/VALSET for GPS L5 enablement
(`0x10310004`) and GPS L5 health override (`0x10320001`), with explicit choice of
RAM/BBR/flash and separately authorized GNSS-only restart. Preserve original
layer values before changing them, verify ACK and per-layer readback, then prove
actual L5 signal usage. Restore each original layer and apply the documented
restart for rollback. Do not run Arduino sample initialization wholesale: it
also changes protocols, rates and other settings. This v1 supplies this manual
procedure, not an automatic health-override or persistent-write command.
