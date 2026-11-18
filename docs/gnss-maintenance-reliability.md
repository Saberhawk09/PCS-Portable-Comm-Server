# GNSS maintenance reliability qualification — 17 November 2026

See [the final closeout](gnss-final-closeout.md) for the subsequent real power-up,
read-only SBAS checks, final validation and individual acceptance decisions.
The measurements below remain the detailed maintenance evidence.

## Operator outcome

The investigation found reproducible receiver-state and snapshot limitations:
the pinned 159-entry database is rejected over retained navigation data, but
accepted after an explicitly authorized software cold start. A newer 199-entry
database is rejected even after cold start. Live overlay restoration is blocked.
The experimental manual path is Advanced → Acquisition Testing, Cold start +
Saved receiver database, with the existing interruption confirmation. Cold start
is required for that path but is not sufficient to guarantee acceptance. Normal
startup never silently clears navigation data to satisfy a restore policy.

The historical snapshot failure to recover PPS within 120 seconds was **not
reproduced**. One stopped-receiver interval did produce approximately 244 kernel
PPS events per second. Its cause is unresolved. Stricter success checks and the
qualification holds contain the risk; they are not a claimed electrical fix.

| Unattended feature | Final qualification | Activation |
| --- | --- | --- |
| Startup database restoration | Unqualified: retained-state and some cold-start restorations rejected | Disabled |
| Clean-shutdown navigation snapshot | Unqualified: unexplained stop-associated PPS events | Disabled |

Qualification remains independent and exact-receiver bound. No qualification
flag, boot policy, receiver flash setting, UART rate, GPIO configuration or
ordinary UTC/Chrony admission rule was changed. No new paid service or Thingstream
request was used. A software cold start is not a complete electrical power cycle.

## Corrective changes

* Maintenance success now requires fresh validated UTC, a new kernel PPS event,
  three consecutive 0.8–1.2 second PPS intervals with exactly one event each, and
  paired, synchronized, selected F10P. A burst or reset counter cannot satisfy
  the cadence check. The existing recovery bound is 120 seconds.
* Recovery reports distinguish UTC, first new PPS, stable 1 Hz PPS and F10P
  selection, measured after GPSD ownership returns. Missing observations remain
  unknown. Public JSON/CSV/text exports retain only bounded numeric evidence.
* The root action and worker reject legacy live database overlays before taking
  serial ownership. Saved database benchmarks require an explicit cold start.
  The worker observes a new NAV-STATUS session before stopping the engine and
  restoring the database; CFG-RST acknowledgement is not assumed.
* Coarse time is offered while that new session is running, before the database
  stop/restore/start sequence. The installed-worker test exposed rejection of
  MGA-INI when offered after engine stop. Failed operations now attempt bounded
  timing recovery after handover, preserving the original failure. Cancellation
  limits this observation to 30 seconds within the systemd stop budget; other
  failures allow 120 seconds. Neither path resets the receiver.
* Restore errors still stop at the first rejected/missing acknowledgement and
  preserve accepted/rejected/unacknowledged counts and the failed entry. Unknown
  code 255 remains a rejection. There is no whole-archive retry or automatic
  receiver-reset workaround.
* Root qualification admission separates shutdown evidence from restore evidence
  and preserves independently admitted results. Neither hold was lifted here.

Changes are confined to maintenance/acquisition, root action admission, the
existing control-panel renderer, parser/worker fixtures and this report. GPSD
remains the sole UART owner outside the existing bounded maintenance handover.
Downloader locks never overlap receiver ownership. Timing fallback and cleanup
continue through the existing journal, lease and systemd StopPost mechanism.

## Real hardware evidence

NEO-F10N, EXT SPG L1L5 6.00, protocol 40.00, 115200 baud, GPIO26 kernel PPS.
Tests used the same antenna location. All complete baseline snapshots had matching
dump-completion counts, unchanged configuration readback and no recovery reset.
Times below start at the receiver restart command; product recovery reports use
the later GPSD handover boundary and must not be compared as identical metrics.

| Stop/dump/start trial | Entries | Dump seconds | First new PPS seconds | Valid UTC seconds | Selected F10P seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 209 | 1.595 | 3.743 | 9.116 | 9.883 |
| 2 | 212 | 1.628 | 3.727 | 9.079 | 9.863 |
| 4 | 215 | 1.656 | 3.794 | 9.089 | 9.089 |
| 5 | 215 | 1.654 | 3.795 | 9.058 | 9.830 |
| 6 | 215 | 1.663 | 3.294 | 9.202 | 9.202 |

Trial 3 aborted on busy Autonomous generation before stopping the receiver or
dumping data. It is not a completed snapshot or a PPS recovery failure. The
harness subsequently waited up to 30 seconds for idle generation.

During trial 5 the kernel count increased by about 738 events while the receiver
was stopped, with 61–62 events per 0.251-second observation. The maintenance
lease held GNSS references withdrawn throughout. Normal 1 Hz PPS and F10P then
returned. This establishes an association with the stopped interval, not whether
the receiver, wiring, GPIO input or kernel generated the unexpected events.

A further instrumented snapshot completed 187 entries, with PPS in 4.658 seconds
and F10P in 11.019 seconds. MON-HW3 pin direction/peripheral masks were unchanged.
Ninety-one read-only GPIO26 samples during three stopped seconds all reported
input, no bias, low; the kernel count did not advance during that interval. This
probe did not reproduce or explain the burst. GPIO pulls/output and TIMEPULSE
drive were preserved because of the shared SAFEBOOT connection.

### Database restoration

All comparisons used a pinned complete 159-entry backup from the exact verified
receiver. Original integrity, age and identity metadata were checked each time.

| Procedure | Receiver acceptance | F10P recovery after restart |
| --- | --- | ---: |
| Retained navigation, normal pacing | 51 accepted; entry 52 rejected, type 0 / status 255 | 9.742 s |
| Retained navigation, extra 50 ms per entry | Same entry and rejection | 9.440 s |
| Explicit software cold start, no host coarse time | All 159 accepted | 69.684 s |
| Early start after explicit cold reset, independent coarse time, trial 1 | All 159 accepted | 48.733 s |
| Same, trial 2 | All 159 accepted | 42.899 s |
| Same, trial 3 | All 159 accepted | 45.634 s |

The earlier extended campaign failed at entry 53; this campaign failed at 52.
Do not describe the failure as an invariant archive entry. Extra pacing did not
resolve it. Cold-start acceptance supports a state-dependent restriction but
does not establish a database capacity, Autonomous conflict or firmware defect.
The no-coarse-time cold trial spent time with an invalid fix and nonadvancing
PPS before ordinary acquisition completed; it was not a valid-UTC/PPS-only stall.
Independent coarse time assists acquisition and never becomes trusted UTC.

Two receiver-running dump candidates failed strict validation. One exposed a
checksum-valid 760-byte MGA-DBD payload, exceeding the documented 164-byte
maximum. The candidate was rejected, the last valid backup retained and normal
timing recovered. The product retains stop/dump/start; validation was not relaxed
to accommodate undocumented firmware output.

Two installed-worker snapshots subsequently captured 201 entries each and
returned to selected F10P in 7.576 and 7.562 seconds after GPSD handover. Stable
cadence was observed in 7.372 and 7.358 seconds. The first installed cold database
trial exposed a separate ordering defect: its only offered assistance frame was
coarse time, rejected after engine stop; no database entries were sent. GPSD
ownership returned, ordinary acquisition subsequently recovered UTC/PPS/F10P,
and the saved backup remained intact. The corrected ordering follows the three
successful early-start investigative trials above. The failed trial is retained
as failed evidence, not relabeled as a successful restore.

With corrected ordering, the installed worker accepted all 201 database entries
and one coarse-time frame, without rejection or missing ACK. Receiver TTFF was
17.093 seconds, host-observed valid 3D 39.069 seconds, UTC 40.061 seconds and F10P
47.425 seconds from reset. A further 2.574-second cadence check passed before
completion. Acceptance does not establish which restored orbit records the
receiver used or demonstrate improved absolute PPS accuracy.

A real cancellation after snapshot handover preserved its Cancelled outcome,
returned sole GPSD ownership and observed stable paired F10P in 5.721 seconds of
bounded cleanup. It did not reset the receiver.

The next installed cold trial used a newer, complete 199-entry same-receiver
snapshot. It accepted coarse time and 61 database entries, then rejected database
entry 62 with code 255. Transfer stopped immediately; ordinary UTC, stable PPS and
F10P recovered in 34.574 seconds after handover without intervention. The bounded
failure-recovery path retained Failed status and exact numeric transfer evidence.

The same 199-entry backup was pinned and repeated with the instrumented
three-second startup wait used by earlier successful trials. It again rejected
entry 62 after 61 accepts with matching type 0 / code 255. F10P returned in
35.954 seconds after receiver restart. Configuration readback was unchanged.
Both the exact failed frame and observed matching acknowledgement are preserved
privately. The backup has valid framing/checksums, a matching completion count,
no duplicate frames, and the correct identity/firmware. Its failed payload differs
from the corresponding-prefix record in the older 201-entry snapshots. Opaque
DBD contents cannot be given an undocumented interpretation. The longer wait
did not resolve this rejection, and neither capacity nor content/state conflict
has been established as the cause. Generic cold restoration remains unqualified.

## Evidence, compatibility and remaining work

The reviewed primary references are [F10 SPG 6.00, protocol 40.00 R02](https://content.u-blox.com/sites/default/files/documents/u-blox-F10-SPG-6.00_InterfaceDescription_UBX-23002975.pdf)
and [NEO-F10N Integration Manual R04](https://content.u-blox.com/sites/default/files/documents/NEO-F10N_IntegrationManual_UBXDOC-963802114-12193.pdf).
DBD is opaque, firmware-specific and bound to its originating receiver. The
documented acknowledgement codes do not define 255. TIM-TP predicts the next
TIMEPULSE and cannot prove that a physical edge reached GPIO26. Restoration is
described after restart; these results do not qualify arbitrary retained-state
or unattended early-start restoration.

Root-private evidence is under
`/var/lib/pcs-gnss-qualification/reliability-20261117`. It includes exact failed
entries and matching acknowledgements, receiver event timelines, configuration
readback, kernel counts/timestamps, GPSD ownership, validator lease and Chrony
observations. Raw receiver identity, location, payloads and credentials are not
public exports. Trial units have bounded execution and the established recovery
StopPost. Full suites use unprivileged processes with devices hidden.

Automated validation: 1,201 ARM64 Python tests, four expected skips, no failures;
146 Windows GNSS tests, six expected skips; all 19 frontend
JavaScript tests passed. Fixtures cover burst PPS, absent/reset counters,
invalid UTC, missing Chrony pairing, malformed/missing ACK, timeout, identity,
ownership, partial transfer, cancellation, redaction and independent qualification
admission. Fixtures are not substituted for the hardware results above.
The final ARM64 run completed in 99.666 seconds. Browser screenshots and DOM
width checks covered 360, 768 and 1440 pixels without horizontal page overflow;
the experimental-restoration warning was also inspected at phone width. The
ordinary hardware self-test returned zero with ALL ENABLED CHECKS PASSED.

Direct electrical observation during a reproduced failure may be needed to
separate receiver output from GPIO/kernel behavior. A sanitized u-blox support
reproduction should include firmware/protocol, stop/start ordering, rejected
entry number and ACK code, without private navigation payloads or identifiers.
No support message has been sent. No shutdown/reboot qualification is claimed:
the underlying snapshot behavior remains under hold, so a further outage would
not justify enabling unattended shutdown saving.

## Installation and rollback

The authorized predecessor safety patch `ad347fb` was installed as a verified
22-file overlay; only its web renderer restarted. The checkout remains clean
released v2.5.1 at `1ddc585920d9877c76b3516fddbce0d2650434be`; no existing release
tag is rewritten. The predecessor receipt is
`/var/backups/pcs-gnss-safety-ad347fb/receipt.json` and its newest installer backup
is `/var/backups/pcs-gnss-info/20261117T162722-14619d9f`.

Reliability corrections `ee817c4` and `1ee6c30` were subsequently installed and
hardware tested as recorded overlays. The final warning/report overlay uses
`/var/backups/pcs-gnss-reliability-final-20261117/receipt.json`, which records the
exact installed commit, manifest backup and full ten-layer reverse rollback
chain. All 22 installed files are checked against the manifest. Each installation
preserves the 62 protected files and monitored timing/other-service PIDs; only
the web renderer is restarted when its loaded module changes. The worker-only
ordering correction required no service restart. No deployment step sends a
receiver command or changes a normal boot policy.

For any later corrective installation use the qualified exact-manifest installer
and retain its receipt with the complete reverse hash-verified chain. Preserve
configuration, credentials, cache/usage, qualification holds, navigation backups
and history. Finish/cancel maintenance and settle journals before rollback. Run
the existing installer rollback against the newest receipt, restart only the web
renderer if its module changed, then verify sole GPSD ownership, fresh UTC,
stable advancing PPS and paired selected F10P. Do not roll back private state or
reenable qualification merely to match older code. PR #112 is the focused review
path; unresolved hardware causes remain explicit release limitations.
