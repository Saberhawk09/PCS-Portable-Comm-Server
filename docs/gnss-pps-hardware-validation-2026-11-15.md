# F10 GPIO26 PPS commissioning — November 15, 2026

This is new, limited appliance evidence. It does not change historical simulated
test results or establish absolute receiver/LAN-client accuracy. It follows the
[USB source cutover](gnss-f10-cutover-2026-11-15.md).

## Physical startup and PPS admission

The operator connected F10 PPS to GPIO26/pin37 with the receiver USB unplugged,
then booted PCS. Before reconnecting receiver USB, GPIO26 was independently
verified input/no-pull and its kernel `pps-gpio` device was positively identified.
The operator then connected USB. GPSD recovered automatically through the
enrolled alias despite renumbering from ttyUSB0 to ttyUSB3. The new boot ID was
`aed6cf00-6d1b-4829-827b-5b5bc9127e4c`.

With valid advancing F10 UTC/fix, `ppstest` and kernel assert timestamps confirmed
60 consecutive one-second events. Host-clock intervals were 0.999380979 through
1.001025790 s while the USB-disciplined system clock was still settling. These
are host-clock interval observations, not measured receiver jitter or PPS accuracy.

A separate backed-up transaction enabled `pps_enabled=true`, updated the
activation digest and generated exactly one F10N USB SOCK reference and one F10P
kernel PPS reference with `lock F10N maxlockage 2`, initially `noselect`. The
validator admitted them only with valid UTC and advancing pulses. Chrony selected
F10P; no `trust` option or EM7565 timing source was introduced. Protected modem,
uplink, position and radio services retained their process identities.

Private transaction, native PPS log and observation evidence:
`/var/backups/pcs-f10-pps-txmrhibq/`. To restore the preceding F10 USB-only timing
configuration without returning to the modem:

```sh
sudo python3 /var/backups/pcs-f10-pps-txmrhibq/commission-pps.py rollback /var/backups/pcs-f10-pps-txmrhibq
```

The earlier full receiver-cutover rollback is separate. Reversing PPS software
does not remove the physical wire or change boot configuration.

## Display and satellite-count corrections

The operator observed a matrix GNSS warning and LCD degraded-PPS warning after
Chrony selected F10P. Both display services run as pi, so reading root-only
enrollment configuration failed. They now use only the root validator's existing
public runtime projection when enrollment is unreadable. Its freshness limit,
NEO mode, scalar/type and privacy checks remain enforced; missing/stale data still
degrades the indication. Enrollment permissions were not relaxed.

The three display services were restarted; the fan, timing services, modem and
radio processes were preserved during that update. Filtered display output
reported no matrix alerts and the LCD page `GNSS Time / PPS: NEO-F10N PPS`.
The operator independently confirmed both warnings cleared.

Live GPSD SKY reports repeated identical `(gnssid,svid)` identities. GPSD permits
per-signal records for multi-band receivers. PCS aggregates by constellation plus
spacecraft, marking a spacecraft used if any associated signal is used. Distinct
constellations remain distinct even with the same svid; PRN-only reports use an
explicit scoped fallback, and unknown identities are not guessed. Legacy totals
remain supported when no identity detail is supplied. GPSD's original per-signal
data remains available to clients.

The GPIO/display collector, web summary and timing diagnostic producer share this
counting policy. Web sampling uses a bounded two-second window instead of stopping
after twelve messages; mixed output could otherwise end before a complete SKY
cycle. Timing diagnostics retain the fullest report only within the same two-second
freshness window; partial reports cannot erase a full cycle or preserve a stale peak.
The timing diagnostic producer was reloaded after sustained observation, with
fresh UTC/PPS reacquisition verified afterwards. Synthetic coverage verifies duplicate
signals, cross-constellation identity, used-flag aggregation, legacy/unknown data,
and public fresh/stale status access without disclosure of raw/private fields.
At the operator's request, the added `GNSS Time / PPS` routine LCD page was then
removed. The existing GPS status/View/Used page and real degraded-state warnings
remain. Final LCD, web summary and timing diagnostics independently agreed on
39 distinct satellites visible and 17 used in the captured snapshot.

## Completed bounded observation and software checks

The 900-second observation collected 181 samples, all synchronized to paired F10P
with fresh valid UTC. Maximum sampled absolute tracking correction was 11.726 us,
including settling; this is a residual system-clock correction, not absolute accuracy.
Native logs, minute-by-minute tracking/sources/sourcestats/selectdata and the summary
are retained privately. Metadata-only reloads followed; normal validated PPS
selection recovered and no failed units remained.

The final appliance self-test returned `ALL ENABLED CHECKS PASSED`, `PCS STATUS:
OK`, exit 0. Meshtastic completed its scheduled GPSD position update normally;
no update/transmission was forced. Final verification also confirmed matching
raw/selected/LAN positions and coordinate-free public receiver diagnostics.

Final targeted Linux suites: 69 GNSS tests and 96 GPIO tests pass. Native Windows
GNSS runs 69 with three POSIX skips; all 96 GPIO tests pass. Web tests (36),
snapshot tests (eight, one Windows-only platform skip) and six documentation tests
pass. The earlier full offline regression was not rerun or reclassified as physical
acceptance. The earlier two real isolated GPSD/Chrony fixtures used synthetic inputs.

## Acceptance limits

The tested startup order is **Pi first, verified input/no-pull, receiver USB
second**. Simultaneous cold start and other power sequences remain unaccepted:
u-blox documents TIMEPULSE/SAFEBOOT coupling and low-loading behavior at startup.
No new reboot or power-cycle was performed after reconnecting the wired receiver.
Electrical voltage/edge measurements, physical antenna/PPS loss and recovery,
all power-up orders and an independent precision reference remain hardware gates.

Sustained observation and final integration/self-test results are recorded in the
workspace commissioning report. A small Chrony residual offset is not an absolute
accuracy specification. During the F10/display phase, no receiver persistent setting, modem AT/reset, LTE
configuration, primary UART or forced RF transmission was changed.

References: [GPSD spacecraft/signal identity](https://gpsd.io/gpsd_json.html#_prn_gnss_id_sv_id_and_sig_id),
[SparkFun GPIO voltage and PPS pad](https://docs.sparkfun.com/SparkFun_u-blox_NEO-F10N/hardware_overview/),
[u-blox startup behavior, section 3.1.2.3](https://docs.sparkfun.com/SparkFun_u-blox_NEO-F10N/assets/component_documentation/NEO-F10N_IntegrationManual_UBXDOC-963802114-12193.pdf).

## Subsequent authorized EM7565 GNSS shutdown

After the F10 acceptance observations above, the operator separately authorized
GNSS-only AT commands and the necessary brief ModemManager stop/start. The
enrolled Sierra AT interface and firmware SWI9X50C_01.14.02.00 were verified
before maintenance. An initial basic-AT handover failed without GNSS writes;
service recovery succeeded. A settling delay and bounded handshake retry then
allowed the guarded transaction to complete.

`AT!GPSEND=0,255` ended fix sessions; GPSAUTOSTART was set to 0 and CUSTOM
GPSENABLE to 0. Readback confirmed both disable settings and session NONE.
Unrelated customizations, BAND, SELRAT and RXDEN readbacks were unchanged.
GPSSEL remained dedicated mode; no diversity or LTE setting was written.
Protected credentials were excluded from logs/source/arguments.

ModemManager was restored with its original enablement and temporary runtime
masks removed. LTE returned registered/attached without a data session. Active
network connections and protected GNSS/GPSD/Chrony/uplink process IDs matched
the preflight. A transient inactive Starlink DHCP row disappeared; this is
recorded separately and no network setting was changed. Legacy NMEA remains
inactive and unowned. F10 UTC/PPS stayed paired with Chrony selecting F10P,
and raw/selected/LAN position endpoints matched. Final appliance self-test:
exit 0, ALL ENABLED CHECKS PASSED, PCS STATUS: OK. Seven focused command/readback
parser tests pass; no new RF or absolute-accuracy acceptance is claimed.

Private evidence: `/var/backups/pcs-em7565-gnss-otmawogp/`. Original GPSENABLE=1
and GPSAUTOSTART=2,1,180,50,1 are captured for a supervised authenticated rollback.
Restoring only the previous PCS software source no longer re-enables modem GPS.
Auto-start changes take effect at the next modem reset per Sierra's reference;
no reset or PCS reboot was performed. Post-power-cycle persistence/activation
verification remains open, alongside the F10 hardware gates above.

Reference: [Sierra Wireless command reference, manufacturer-authored revision 4](https://www.bipom.com/documents/GetWireless/41111748%20EM75xx%20and%20EM-MC74x1%20AT%20Command%20Reference%20r4.pdf),
printed pages 25–26, 77–78, 80 and 90.
