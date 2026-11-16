# F10 source cutover — November 15, 2026

The operator explicitly authorized moving all GNSS positioning and timing from
EM7565 to NEO-F10N, with PPS wiring still disconnected. This supersedes the
earlier legacy-default deployment boundary for this appliance only. New and
existing installations without an explicit selection retain the legacy default.

The F10 was positively identified on PCS at 115200 baud, protocol 40.00, using
the enrolled `/dev/pcs-gnss-neo` USB topology alias. No primary UART is used.
GPIO26/pin37 was verified input/no-pull after the previously authorized reboot;
the bound kernel PPS driver and device-tree mapping were identified without
assuming a PPS device number. Physical PPS acceptance remains pending.

Before cutover, 67 advancing valid NAV-PVT/GPSD TOFF epochs were measured against
independent synchronized internet NTP. Median raw serial offset was -63.363 ms,
with observed samples from -72.506 to -57.602 ms. The applied positive offset
compensates USB/reporting delay; configured uncertainty includes observed jitter,
the larger NTP root-distance bound at either end and a 10 ms margin. This is
coarse USB calibration, not a PPS accuracy claim or a permanent calibration.

The explicit `pps_enabled: false` state selects F10 USB for GPSD position and
validated USB time. It retains NTP/local fallback, removes the old GPS SHM
reference, and cannot admit uncommissioned pulses. The manual/selected-position
2948 service and raw 2947 contracts are preserved. No automatic EM7565 failover
is enabled. AUX/GPSSEL work remains deferred.

The first guarded activation found a production service-sandbox problem:
`chronyc` needs to bind its client reply socket under `/run/chrony`, which was
read-only. Narrowly permitting this directory resolved the independently
reproduced authorization error; no clock-setting capability was added. Rollback
admission was also corrected to filter the modem's proven NMEA interface before
checking USB-serial uniqueness. Its other interfaces share that serial number.
The initial conservative rollback withheld GPSD until this was corrected;
explicit recovery then confirmed `legacy_gpsd_restored: true`.

Hardware acceptance still requires measured 3.3 V/rising-edge pulses, at least
60 consecutive kernel events, actual UTC/PPS pairing, independent timing
comparison, sustained Chrony observation and startup/loss/recovery tests. The
NEO TIMEPULSE and SAFEBOOT functions are internally coupled: avoid low loading
at receiver startup. Running input/no-pull does not establish all power-up
orders. Leave SAFE, RESET and EVENT unconnected. See the
[u-blox integration manual, section 3.1.2.3](https://docs.sparkfun.com/SparkFun_u-blox_NEO-F10N/assets/component_documentation/NEO-F10N_IntegrationManual_UBXDOC-963802114-12193.pdf)
and [commissioning runbook](gnss-pps-commissioning.md).

Final appliance results and the exact private rollback directory are recorded
in the workspace deployment report, separately from historical software tests.

Post-cutover checks confirmed F10-only GPSD ownership, matching positions on
raw 2947, selected 2948 and trusted LAN 2947, validated UTC, Chrony `#* F10N`
with reach 377/normal leap status, no validator restarts and no failed units.
All 64 focused Linux tests passed; native Windows skips three POSIX cases.
Both isolated real GPSD/Chrony protocol fixtures passed with synthetic input and
no system-clock control.
The existing self-test retains one failure pending Meshtastic's normal 1800 s
position update: connected gateway, map uplink and privacy checks pass, but
`position_updates` is still zero after the earlier no-fix interval. No update
or radio transmission was forced. PPS degradation is an expected warning.
The self-test now reads the coordinate-free privileged status command instead
of opening private enrollment JSON as the unprivileged operator.
