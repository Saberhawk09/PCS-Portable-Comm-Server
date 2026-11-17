# GNSS dashboard and differential correction reporting

This focused v2.4.1 refinement preserves the commissioned GPSD / F10N UTC /
F10P kernel-PPS architecture and the existing AssistNow downloader, cache,
enrollment, timer and quotas. No new daemon, frontend framework or dependency
is required. The normal collector remains a passive GPSD client.

## Page sections

| Section | Data and controls |
| --- | --- |
| Overview | Six summary cards: fix including 2D/3D DGPS, spacecraft used/visible, horizontal accuracy, selected synchronized reference, correction source/health, Predictive cache. Compact Navigation, Corrections and Time synchronization panels. Services, integrity and detailed timing expand in place. |
| Satellites | Measured sky geometry; L1/L5 C/N0; constellation spacecraft counts; constellation, band, used-only and satellite/signal text filters; collapsed signal table with tracking, health, navigation usage and correction usage; expandable measured count history. |
| RF Diagnostics | Each RF path's AGC, noise and jamming; signal security; one primary spectrum with L1, L5 or center-relative comparison; current/peak/average selection; bounded capture, stop, snapshot and event history. Trace capture timestamps are separate from live telemetry freshness. |
| AssistNow | Cache availability, validity range and next refresh first; Autonomous activity, Live entitlement/activation, download/uplink and injection outcome. Expand usage and the existing refresh, metering, boot-injection, Live, prediction-window, quota and cache controls. |
| Advanced | Firmware, hardware, protocol and enrolled UART; detailed accuracy/DOP/Chrony residuals; correction service and per-satellite data; message availability and decoded diagnostics; confirmed Autonomous maintenance requests; redacted export. Unqualified receiver database backup/restore remains disabled. |

All five sections use authenticated server navigation and work without JavaScript.
Only the selected section constructs its plots and tables. Tables scroll within
their own container; satellite measurements start collapsed. Host assistance
settings retain their previous privileged helper, confirmations and CSRF checks.
Configuration requests do not directly write the receiver from the web process.

## Protocol contract

Verified against [u-blox F10 SPG 6.00 Interface Description, UBX-23002975 R02](https://content.u-blox.com/sites/default/files/documents/u-blox-F10-SPG-6.00_InterfaceDescription_UBX-23002975.pdf),
protocol 40.00, as used by the commissioned NEO-F10N.

* NAV-PVT flags `diffSoln` means differential corrections are **applied**; valid
  base 2D/3D fixes gain the DGPS label. NAV-STATUS separately reports applied
  differential solution and `diffCorr` availability.
* NAV-SBAS (01-32) has exactly `12 + 12 * cnt` payload bytes, without a version
  byte. Its signed system field decodes -1 Unknown, 0 WAAS, 1 EGNOS, 2 MSAS,
  3 GAGAN and 16 GPS. Unrecognized codes stay Unknown with their numeric code
  retained. GEO PRN zero means unavailable. Services include ranging,
  corrections, integrity, test mode and bad signal; integrity-used status and
  receiver mode remain separate. Per-GPS-satellite pseudorange and ionospheric
  corrections are signed centimetres, displayed in metres. UDRE is exposed as
  the reported monitoring code without inventing an error bound.
* NAV-SIG exposes reported correction source and PR/carrier/Doppler correction
  application independently of navigation measurement usage. NAV-SAT exposes
  correction availability and SBAS/RTCM/SLAS/SPARTN/CLAS usage. Orbit prediction
  availability is never counted as DGPS.
* Source identification requires fresh, used correction indicators aligned to
  NAV-PVT within two seconds (including GPS-week rollover). SBAS reception alone
  cannot establish the source of an applied differential solution. Missing,
  stale or mismatched system information never defaults to WAAS.
* Observation age is **not correction age**. NAV-SBAS does not report correction
  age; the page says so. Test/bad-signal flags produce a warning. A current valid
  differential solution, received SBAS services and integrity availability have
  distinct meanings. Receiver-reported integrity does not establish authenticity.

Geometry is joined to signal rows only for fresh, corresponding epochs. The
existing v2.4.0 spacecraft-count function and GPSD SKY selection remain intact.
Plots contain measured points without smoothing or interpolated samples.

## Optional supervised NAV-SBAS output qualification

First inspect passive GPSD output. If NAV-SBAS is already emitted, no receiver
configuration is needed. Otherwise the only new setting is
`CFG-MSGOUT-UBX_NAV_SBAS_UART1` (`0x2091006b`, U1), rate 1 per navigation epoch.
No baud, constellations, timing, Autonomous, ITFM, reset or assistance commands
are part of this change.

`scripts/qualify-gnss-sbas.py` is an explicit, root-only commissioning tool, not
an installed background service. It checks the enrolled USB identity, exact
receiver binding, protocol, UART1 115200 and existing one-Hz navigation. It reads
RAM/BBR/flash configuration before writes, protects all existing GNSS and
diagnostic settings, and journals only the SBAS rate. The baseline cannot be
overwritten by rerunning inspect. Sparse persistent-layer overrides are restored
with VALDEL where appropriate; flash writes are suppressed when already equal.

Run each phase only under a bounded transient systemd maintenance unit with
`Type=oneshot`, `TimeoutStartSec=35`, `TimeoutStopSec=20` and
`ExecStopPost=/usr/bin/python3 STAGED_TOOL resume`. Use a unique unit name.
Its private state directory is
`/var/tmp/pcs-gnss-refinement-20261117/receiver-maintenance`.

1. `inspect` saves the original layers and verified identity privately.
2. `ram --confirm 'I authorize NAV-SBAS output maintenance'` applies only RAM.
3. Observe checksum-valid NAV-SBAS through GPSD; verify correction decoding,
   one-Hz output, ownership, protected settings and F10N/F10P recovery.
4. Only with the operator's persistence authorization, use
   `persist --confirm 'I authorize NAV-SBAS output maintenance'`; read back RAM,
   BBR and flash, then repeat passive/time verification. This never resets the
   receiver. Flash readback does not substitute for a physical power-cycle test.

The installation lock excludes other receiver management. GPSD explicitly
releases the enrolled device via its local control socket before the existing
exclusive admission helper opens it. The same GPSD PID regains sole ownership
afterwards. ExecStopPost restores a pending transaction after interruption,
checks the bound receiver and returns ownership. GPSD and Chrony are not
restarted. The UTC freshness gate may briefly withdraw F10N/F10P during a
maintenance handover; kernel PPS continues and Chrony retains its existing
holdover/fallback policy. Routine diagnostics never perform this handover.

Rollback the message rate, if needed, in the same bounded workflow with
`restore-original --restore-confirm 'I authorize restoring the pre-refinement NAV-SBAS output'`.
Do not use a software rollback to overwrite receiver configuration implicitly.

## Software staging and rollback

Use the existing `stage-gnss-intelligence.py` installer and its exact manifest.
Compare all installed hashes first: only `pcs_gnss_diagnostics.py`,
`pcs_gnss_info.py` and `pcs_control_panel.py` should change for this refinement.
The maintenance tool stays in root-owned staging. Preserve the existing protected
main checkout and develop on `codex/gnss-intelligence-v241`.

The installer saves an atomic receipt under `/var/backups/pcs-gnss-info/` and
does not start or restart services. After deployment restart only
`pcs-gnss-info.service` and `pcs-control-panel.service`; the existing web redirect
dependency may follow the latter. Check sole GPSD ownership, unchanged GPSD /
Chrony / UTC-gate process generations, fresh UTC, paired selected F10P, existing
cache and policy, failed units and unrelated service state.

For software rollback, run the staged installer `rollback --directory RECEIPT`
with `--confirm 'I authorize PCS GNSS software installation'`, then restart only
those same two services and verify recovery. This restores the previous GNSS
Intelligence implementation, not the v2.4.0 base or a different enrollment.

## Verification

`test_gnss_corrections.py` covers standard/differential fixes, SBAS systems,
signed centimetres, malformed lengths, missing/stale/out-of-epoch sources,
service/integrity warnings, week rollover, correction-vs-orbit separation,
spacecraft counting, section rendering, filters, preserved controls, one spectrum
per view and export privacy. `test_gnss_sbas_maintenance.py` covers the exact
one-key write allowlist, protected settings, RAM-only writes, persistence,
readback failure, sparse-layer rollback, interruption recovery data and duplicate
write suppression. Existing HTTP security tests exercise sessions, CSRF,
redaction and post-action section redirects.

Browser acceptance uses actual screenshots and DOM geometry at 360, 768 and
1440 pixels, covering all five sections, expanded tables/settings and RF view
switching. Preview data is explicitly labeled synthetic protocol fixtures;
production uses only receiver measurements. Live acceptance additionally checks
real NAV-SBAS and passive spectrum capture after installing the validated files.
Absolute PPS accuracy, electrical and cold-power-cycle qualification remain
separate physical tests.

Expected incremental overhead: one small 20 + 12*cnt-byte UBX frame each second;
bounded correction rows in the existing snapshot; negligible decoder work; no
new network assistance requests, cache quota, dependencies or persistent daemon.
The default page constructs no SVGs. Final deployment evidence records measured
CPU/RSS, UART traffic, service continuity and the specific rollback receipt.
