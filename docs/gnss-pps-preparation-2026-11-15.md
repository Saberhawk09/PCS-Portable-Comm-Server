# NEO-F10N local preparation — 2026-11-15

Scope: local development and packaging only. PCS is offline; the receiver still
requires physical installation, actual Pi USB topology enrollment and PPS wiring.
No connection attempt to PCS, appliance command, deployment, source switch,
reboot, boot edit, modem action, radio transmission or receiver write occurred
for this revision. Instructions in the supplied handoff were treated as technical
requirements within the user's local-only boundary, not as deployment authority.

## Baseline and smallest update

The existing `codex/gnss-pps-v232` worktree was clean at
`6257bc9f802dc15f4026dd6ce34b3e6e4e2ed010`. Current GitHub main was checked through
the connector and remains v2.3.2 `7873faaec908c10e5d86e824e8097d6068561c2f`;
the candidate already contains it, so no upstream merge was required.
The earlier local deployment report records the November 12 installation in
legacy mode. That is historical evidence, not a fresh inspection of offline PCS.

Existing UTC gating, PPS identity, Chrony F10N/F10P pairing, private status
projection, manual-position architecture and guarded activation/rollback were
retained. Changes are limited to the delivered profile and deployment preparation:

- Host baud discovery supports 115200 and factory 38400 on one explicitly selected
  unowned port, with exclusive UBX model/protocol and UART1 baud readback. It never
  writes baud, scans unrelated serial ports or claims a CH340 VID/PID proves model.
- Startup hands actual detected baud to GPSD through a root-owned runtime
  environment. Queueing reconnect restarts avoids blocking the validator watchdog
  while the two-speed probe runs. Wrong identity/ownership fails closed.
- The timing profile excludes baud and NAV-TIMELS changes; diagnostic message
  rates, NMEA selection, signals and other settings are preserved. Only explicitly
  consented required timing RAM differences can be written, with ACK/readback and
  restoration on failure. No persistent writes or reset are added.
- The real operator exports are unmodified fixtures. Offline comparison confirms
  380 RAM items each, 17 changed values, 363 unchanged values and only 11 BBR plus
  11 flash entries. The finished export needs zero required timing RAM changes.
- NAV-SIG count/age and NAV-TIMEUTC/TIM-TP report ages enter the existing status
  projection and diagnostic card. They do not authorize UTC/PPS or expose raw
  packets/coordinates. Advancing NAV-PVT/TOFF remains the time gate.
- The source CLI now prioritizes its own policy library, so a candidate can be
  staged beside the previously installed version without importing stale code.
- Documentation uses the Waveshare 25346 puck, reported firmware/hardware,
  115200 mixed UBX/NMEA profile and one selected receiver. EM7565 AUX is deferred;
  the existing modem GNSS path remains an explicit rollback only.

The dedicated device path will be `/dev/pcs-gnss-neo`, not a fixed kernel
`ttyUSB` number. Its actual serial or Pi `ID_PATH` binding remains deliberately
unset until installation. The public example still contains an unenrolled path
and null timing calibration and therefore cannot activate.

## Evidence and validation

Export SHA-256 values (also checked against the original Downloads files):

- `stock-f10-neo.ucf`: `bf8e5386f704d3ed2c0642cdc0f541c14cb92ee0b5789b46ca4f1e564ab9262f`.
- `f10-neo-pcs-finished.ucf`: `2cecec00439bf04952070d080ddcf8c514586be2f78af969ff668c4b92fe114d`.

The focused suite includes the original 42 GNSS tests plus 12 delivered-profile
and baud tests. New coverage includes mixed RAM/persistent evidence, exact
firmware metadata, no writes for the finished profile, required-only differential
RAM changes, explicit-baud behavior, factory-baud recovery, no retry on a wrong
model, signal metadata expiry and real Linux PTY/termios query exchange. Existing
tests retain stale/repeated/invalid UTC, wrong-second/device pairing, PPS loss,
USB renumber/reconnect, network/local fallback, rollback and consumer contracts.

Validation completed in the disposable Debian 13 VM:

- Full Python regression: **1,032 run, 1,028 passed, four expected Windows-only
  skips**. No hardware acceptance is implied by these totals.
- Focused GNSS cases: 54 included in the Linux regression, including the real
  PTY baud-recovery test. Native Windows focused run: 54 run, 51 passed, three
  POSIX-only skips.
- Enhanced real GPSD mixed NMEA/UBX forwarding and Chrony pairing/rejection
  protocol fixtures: two passed after the full suite.
- Actual systemd pre-start environment handoff: both baud cases passed.
- Existing JavaScript suite: 19 passed. Python compilation and GNSS unit-file
  verification passed; documentation link tests (six) and diff checks passed.

These results and logs are retained with the final local package. The systemd test
creates only disposable fixture units and proves that both detected baud values
reach ExecStart after ExecStartPre. It does not prove production GPSD confinement,
Pi service ordering or hardware. The protocol tests use synthetic PPS samples
and Chrony without system clock control, as in the original development evidence.

An initial regression snapshot had two documentation-link failures because its
upload preceded completion of the new install guide. The complete snapshot was
validated again; do not treat those initial failures as hardware evidence or as
accepted outstanding failures. No historical test totals were rewritten as new
acceptance. Final counts and archive identity are in the local package receipt.

## Future gates and operator instructions

Use [the operator install sequence](gnss-pps-install.md) and
[phase commissioning criteria](gnss-pps-commissioning.md). Software staging,
physical USB installation/exclusive queries, GPIO/boot/reboot and source cutover
remain separate operator-approved operations. No base installer is needed.

Still unverified: actual Pi model/protocol/firmware and USB path, current PCS
services/package state, effective settings after cold power loss, live message
rates/USB bandwidth, antenna bias/placement and LTE baseline, GPIO26 ownership
and both power-up orders/SAFEBOOT safety, >=60 kernel PPS events, >=15 minutes of
Chrony statistics and independent offset comparison, source loss/recovery,
production systemd/AppArmor behavior, physical rollback and all PCS consumers.
Bench L1/L5 reception does not prove PPS or LAN NTP accuracy. EM7565 AUX remains
deferred and unqualified, with no AT/authentication/reset sequence in this plan.
