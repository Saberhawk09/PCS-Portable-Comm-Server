# Final GNSS reliability closeout — 17 November 2026

## Operator decision

Retain the commissioned GNSS/PPS timing system and the installed reliability
overlay. Keep automatic receiver-database restoration and shutdown snapshots
**unqualified and disabled**. Normal operation does not need either feature.
The evidence supports containment, not a claim that both receiver problems have
been fixed. No additional reset campaign is justified for this closeout.

This report supersedes earlier statements that the recovery follow-up was still
pending installation. It supplements the detailed
[maintenance investigation](gnss-maintenance-reliability.md) and
[extended acquisition qualification](v2.5.0-extended-qualification.md).

| Final acceptance question | Answer |
| --- | --- |
| Is PPS recovery reliable after navigation maintenance? | Five completed baseline snapshots recovered selected F10P in 9.089–9.883 s after receiver restart, without intervention. Two installed-worker snapshots recovered in 7.562–7.576 s after GPSD handover. However, the earlier valid-UTC/PPS-only outage was not reproduced or explained, and a stopped interval produced unexpected kernel edges. Supervised maintenance only; no general unattended reliability claim. |
| Exactly when does database restoration work? | The pinned 159-entry backup passed after explicit software cold start, including three early-start repetitions; a 201-entry backup also passed. Retained-state restores failed, and a newer valid 199-entry backup failed twice even after cold start. Cold start is required by the experimental manual path but is not a sufficient compatibility guarantee. There is no generally qualified restore procedure. |
| Enable automatic shutdown saving? | **No.** Its independent qualification hold remains. Verified power availability alone does not resolve the stop-associated PPS anomaly or qualify successful shutdown capture. |
| Enable automatic startup database restoration? | **No.** Its independent hold remains. Never silently clear navigation data to make restoration possible. |
| Recover after actual power-off? | **Yes, on the observed operator power-up after several hours off:** receiver TTFF 4.312 s and F10P selected 24.561 s after kernel boot. Retained receiver data was not proven cleared. |
| Importance of remaining issues? | Database preservation is optional, but its unexplained timing side effects are important if maintenance is invoked. Disabling automation contains that exposure. SBAS availability is a secondary positioning limitation. |
| Ready for long-term field use? | Normal commissioned timing, retained/Autonomous data and Predictive cache operation are the recommended field configuration. Experimental database operations are excluded. Electrical fault isolation, prolonged field endurance, signal-loss recovery and independent timing accuracy retain explicit deferred gates; this is not unconditional acceptance of every field condition. |

## Architecture and operating defaults

* The enrolled `/dev/pcs-gnss-neo` remains at 115200 baud. GPSD is the sole normal
  UART owner, with read-only configuration. Raw GPSD uses loopback 2947;
  selected/manual positioning remains on 2948 and trusted LAN sharing is separate.
* GPIO26 kernel PPS is admitted only with fresh validated F10N UTC and the
  existing F10P pairing rules. Receiver time accuracy, PPS cadence, Chrony offset
  and position accuracy are different measurements. TIM-TP is not physical edge
  evidence. TIMEPULSE/SAFEBOOT wiring and settings remain unchanged.
* Existing exclusive maintenance withdraws GNSS references, hands ownership
  away from GPSD, verifies receiver identity, performs bounded operations, restores
  temporary RAM settings and returns ownership. StopPost recovery and private
  journals remain authoritative. Downloads never hold receiver locks.
* The acquisition policy file remains absent, preserving commissioned cache-only
  startup admission. Ordinary acquisition starts immediately; healthy timing
  defers host injection. Late assistance can refresh a future cache without reset.
* Autonomous remains enabled. Predictive uses a seven-day GPS/Galileo archive,
  current-UTC-day compatible MGA-ANO selection, 72-hour refresh and the existing
  1 MiB/day payload cap. Metered/unknown uplink use is already authorized within
  that cap. Existing request/monthly limits and durable reservations remain
  unchanged; TLS/DNS traffic is additional. No uplink is activated for assistance.
* Live Orbits remains disabled; no paid requests were made. Position-dependent
  assistance remains off. Downloaded data never supplies trusted UTC.
* Pi navigation databases and downloaded orbit archives remain separate private
  stores. Shutdown saving and automatic database restoration remain off under
  exact-receiver root-owned qualification holds. The shutdown unit being enabled
  does not override these gates. Sudden power loss cannot create a host backup.

## Individual acceptance states

Qualified means the stated scope passed real-hardware observation, not that all
possible physical or environmental conditions have been exhausted.

| Capability | State | Scope / restriction |
| --- | --- | --- |
| Normal F10N UTC / GPIO26 PPS / paired F10P | Qualified | Commissioned pipeline and sustained startup/recovery observations; strict trust gate retained |
| Observed power-off startup and RTC seed | Qualified | One actual power-up after hours off; RTC seed completed without warning, later check passed |
| Passive GNSS, L1/L5, RF/security and spectrum | Qualified | Actual fresh telemetry and two measured spectrum paths; diagnostic, not calibrated RF equipment |
| Predictive cache and ordinary offline acquisition | Qualified | Compatible host cache, real assisted starts and observed retained-data startup; no paid dependency |
| Hot/warm/software-cold benchmark controls | Restricted | Confirmed supervised maintenance only; software reset is not electrical power loss |
| Manual stop/dump/start navigation snapshot | Restricted | Complete-count validation and recovery checks passed; unexplained historical PPS outage prevents unattended acceptance |
| Saved-database restoration | Restricted | Experimental confirmed cold benchmark only; some valid backups reject. Live overlays and warm/hot database trials blocked |
| Automatic startup database restore | Unqualified (disabled) | Generic same-receiver restoration is not reliable across tested backups |
| Automatic shutdown navigation saving | Unqualified (disabled) | Independent hold; stop-associated PPS behavior unresolved, no successful full shutdown qualification |
| Running-engine database dump candidate | Unqualified (disabled) | Invalid/oversize output rejected; production validation not relaxed |
| DGPS/SBAS reporting | Restricted | Earlier actual WAAS/PRN 131 observed; current NAV-SBAS disabled despite active enable settings. No correction source inferred |
| Internet NTP fallback admission | Restricted | Existing configuration and failure fixtures retained; reachable alternatives present, but brief closeout handover showed Unsynchronized rather than selected Internet NTP |
| Physical antenna/USB/PPS loss and restoration | Deferred | Requires a separate controlled physical campaign; maintenance withdrawal and fixtures do not establish electrical loss behavior |
| Independent LAN/absolute NTP accuracy | Deferred | No verified independent reference available; Windows time service stopped. No host clock or network reconfiguration attempted |
| Live Orbits | Unqualified (disabled) | No verified paid-service hardware campaign or billable requests |
| Periodic navigation snapshots | Unqualified (disabled) | Repeated unnecessary timing interruptions are not qualified |
| Long-duration field endurance / electrical PPS isolation | Deferred | Requires field observation and suitable electrical equipment |

## What was corrected, and what remains unknown

The installed worker requires a genuinely new kernel event, three consecutive
1 Hz intervals, fresh validated UTC and synchronized paired selected F10P before
success. It reports the failed recovery stage and separate recovery durations.
Failure/cancellation preserve their outcomes while observing bounded recovery;
ownership cleanup alone is not timing success. No automatic reset hides a fault.
Database errors stop the transfer at the first rejected/missing matched ACK and
retain counts and the failed entry. Coarse time is sent before engine stop.
Startup and shutdown qualifications are independent.

These are verified safety and sequencing corrections. The cause of the historical
PPS-only stall and approximately 244 Hz stopped-interval kernel event burst is
**unknown**. Existing GPIO samples and TIM-TP cannot distinguish receiver output
from wiring/GPIO/kernel behavior without electrical observation during a failure.
TIMEPULSE and temporary ACKAIDING readbacks were unchanged after completed trials.

MGA-ACK status 255 is **undocumented** in the reviewed protocol. It remains a
rejection, not success. Extra pacing and a longer startup wait did not resolve
the tested failures. State dependence is observed, but capacity, opaque record
content and Autonomous conflicts have not been established as causes. See the
[sanitized manufacturer-support draft](gnss-ublox-support-reproduction.md).
Nothing has been sent to u-blox.

## Actual power-up evidence

The operator powered PCS on after several hours off on 17 November, approximately
22:16–22:26 EST (18 November UTC). Current-boot service timestamps and Chrony
journal recorded:

| Event | Seconds after kernel boot |
| --- | ---: |
| RTC seed completed, no warning | 10.894 |
| Chrony active | 11.502 |
| GPSD active | 15.286 |
| Passive diagnostics active | 15.302 |
| UTC validator active | 15.944 |
| Chrony selected F10P | **24.561** |

NAV-STATUS reported **4.312 s TTFF**, a different time origin from host boot.
First SSH was at uptime 38.32 s, so initial host fix/UTC transitions were not
directly sampled. Later 121/121 observations over 133.16 s remained paired with
sole GPSD ownership. A 60.04 s fast PPS capture measured 59 consecutive intervals,
none missed, ranging 0.999975681–1.000029087 s. This is cadence evidence, not an
absolute accuracy measurement. Host injection was deferred because the receiver
was already fixed; no download or injection occurred during this boot check.
Retained navigation/Autonomous data may have contributed.

The earlier three alternating software-cold pairs measured median receiver TTFF
47.590 s with no external assistance versus 22.666 s with cached Predictive
Orbits: **52.37% lower median TTFF** in that small campaign. RF conditions were
not proven equal and retained Autonomous/flash data was not proven absent.
These are not fully unassisted electrical cold starts and do not demonstrate
better absolute PPS accuracy. No further benchmark was needed for the
documentation-only closeout.

## Final read-only SBAS and health checks

A bounded existing ownership handover read active and factory-default SBAS
settings. Both had SBAS/L1 C/A enabled, ranging and differential correction enabled,
integrity/test/iono-only disabled, PRN mask `0x3ab88` and accept-outside-mask zero.
PRN 131 is in that mask. BBR and flash queries were rejected; their contents are
**unknown**, not inferred absent or disabled. NAV-SBAS still reported Disabled,
no selected GEO and no applied corrections. Its system field alone is not proof
of active WAAS. No changed active configuration was found to explain the earlier
WAAS observation; the runtime availability discrepancy remains unresolved.

The successful readback made zero configuration writes, engine stops or resets.
Protected settings matched before/after. Twenty measured kernel intervals had
no abnormal cadence; monitored service PIDs were unchanged, sole GPSD returned,
and fresh UTC/selected F10P recovered 6.092 s after handover (stable PPS 2.944 s).
An earlier storage-layer rejection also returned ownership; a later strict health
check confirmed recovered timing. During the brief successful lease, the status
projection reported Unsynchronized rather than selected Internet NTP. Do not
claim seamless fallback from that observation. Normal F10P resumed without
intervention; no Chrony policy was changed to force a favorable result.

Final read-only modem checks found the modem through ModemManager and obtained
an actual MBIM device-capabilities response through its proxy, without reset,
registration changes or data-session activation. Position-feed endpoints remained
reachable. RF and spectrum freshness, no failed services and the ordinary PCS
self-test are recorded with the final verification below.

Final live verification found fresh navigation, satellites, signals, SBAS,
RF/security and two 256-bin L1/L5 spectrum paths. MON-VER is not periodically
streamed: its message freshness remains Unknown, while startup admission supplies
verified firmware metadata. All three position endpoints were reachable. The
ordinary PCS self-test returned zero with ALL ENABLED CHECKS PASSED; no systemd
units failed. Sole GPSD ownership, fresh UTC, advancing stable PPS and selected
paired F10P passed the final strict recovery check. Quota state remained two
Predictive requests, zero Live requests and 70,560 downloaded bytes for its
stored accounting day; this closeout made no assistance request.

A five-second `/proc` sample measured passive diagnostics at 22.85 MiB RSS and
2.97% of one CPU core; the existing UTC validator was 16.09 MiB and 47.37% of one
core. These are short observations of existing processes, not an endurance or
incremental-cost benchmark. Service resource accounting was unavailable and was
not enabled. The documentation-only closeout adds zero runtime/network overhead.

## Validation, installed baseline and release candidate

The appliance checkout is clean released v2.5.1 at
`1ddc585920d9877c76b3516fddbce0d2650434be`. The installed safety/reliability overlay
is `9c550ccccdba9c8699651c3c5c19004965696240`. The development baseline
`f4a714f75800af9b94cbbd54c0d0f9640972563d` differs from that overlay only in
documentation. All **22** files in a freshly staged exact manifest match installed
bytes. There is no pending executable recovery patch to redeploy. This closeout
adds documentation only, with no new services, polling, dependencies or runtime
resource cost. Earlier installation receipts retain 62 protected-file checks and
a verified ten-layer reverse rollback chain.

Fresh final-source ARM64 regression: **1,201 tests, four expected skips,
100.466 s, no failures**, in an unprivileged isolated service with live devices
hidden and an empty GNSS runtime bind. Windows GNSS regression: **215 tests,
nine expected skips, no failures**. Frontend JavaScript: **19 passed**. Existing
real-hardware and simulated-fault evidence remains separately identified in the
maintenance report. No fixture is presented as electrical acceptance. Installer
and rollback fixtures run in the suite; live rollback receipts and staged hashes
were verified without undoing the working installation. Text exports used Linux
line endings; source-code content was unchanged.

The release candidate is the existing focused protected-main
[PR #112](https://github.com/Saberhawk09/PCS-Portable-Comm-Server/pull/112).
This closeout does not merge, publish a release, rewrite v2.5.1 or deploy software.
Review [candidate release notes](gnss-closeout-release-notes.md) with the holds
intact before separate release approval.

## Routine operation, recovery and rollback

Use normal GNSS acquisition and the existing cache scheduler. Check the Overview
for fresh UTC, stable PPS and selected F10P; distinguish a valid position from a
trusted timing source. In AssistNow, check cache dates/next refresh and quotas.
Leave database policies, periodic captures and shutdown saving disabled. Avoid
routine database experiments on an operational time server.

If maintenance fails, retain the original failure and private journals. Confirm
GPSD sole ownership, no active lease, fresh UTC, advancing stable PPS and paired
F10P; GPSD reconnection alone is insufficient. Use the existing authorized
Cancel/Recover workflow and bounded StopPost recovery. If timing does not return,
report the exact recovery stage and maintain safe fallback; do not delete identity
guards, force qualification flags or loop receiver resets. Review any separate
modem failure without expanding GNSS recovery into modem/network changes.

The private final deployment receipt is
`/var/backups/pcs-gnss-reliability-final-20261117/receipt.json`, with newest backup
`/var/backups/pcs-gnss-info/20261117T185938-c6c3b1f1`. For an authorized software
rollback, settle maintenance/journals first, verify current installed hashes and
follow the receipt's exact reverse chain to the chosen baseline. The newest layer
alone reverses only that installation. Use the matching installer:

```sh
sudo python3 /path/to/verified/source/scripts/stage-gnss-intelligence.py rollback \
  --directory /var/backups/pcs-gnss-info/20261117T185938-c6c3b1f1 \
  --confirm 'I authorize PCS GNSS software installation'
```

Restart only affected loaded web code when required by the selected layer;
preserve GPSD/Chrony and original service state. Verify all timing criteria and
self-test afterward. Preserve credentials, cache/usage, navigation backups,
absent policy file and qualification holds. See the
[installer runbook](gnss-intelligence-install.md) before executing rollback.
Documentation-only closeout changes require no appliance rollback.

Private evidence remains under `/var/lib/pcs-gnss-qualification/` in
`reliability-20261117`, `startup-20261117-b9a047c9` and `closeout-20261117`.
Credentials, receiver identifiers, coordinates, exact database entries and ACK
payloads remain outside GitHub. These directories contain commissioning evidence,
not material for public diagnostic exports.
