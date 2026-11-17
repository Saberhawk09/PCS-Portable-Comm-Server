# GNSS maintenance reliability investigation

## Deployment and current acceptance

The previously pending safety patch `ad347fb5de3f035ec6f18e69b4059fb3760d9518`
was explicitly authorized and installed on 17 November 2026 using the existing
exact-manifest installer. All 22 installed files verified. Exactly three Python
modules changed: maintenance, acquisition and control-panel rendering. Sixty-two
protected files, assistance usage/cache and qualification holds were preserved.
GPSD, Chrony, the UTC gate and other monitored service PIDs did not change.
Only the web renderer restarted to load the page changes; no receiver command,
reset, flash write, boot-policy change or GNSS service restart was involved.

The verified rollback receipt is
`/var/backups/pcs-gnss-safety-ad347fb/receipt.json`; its newest installer receipt is
`/var/backups/pcs-gnss-info/20261117T162722-14619d9f`. The complete seven-receipt
reverse chain was hash-checked. The appliance checkout remains clean released
v2.5.1 at `1ddc585920d9877c76b3516fddbce0d2650434be`; this installation is a
recorded software overlay, not a rewritten release tag.

Post-installation checks observed sole GPSD ownership, fresh diagnostics and
validated UTC, advancing kernel PPS, selected/paired F10P, active normal
assistance refresh, and no failed services. The exact-receiver qualification hold
is visible in the status consumed by the page. No Thingstream request was made.
The ordinary PCS self-test also exited successfully with all enabled checks
passed.

| Unattended feature | Qualification | Activation |
| --- | --- | --- |
| Startup database restoration | Withdrawn; receiver-state dependency unresolved | Disabled |
| Clean-shutdown navigation snapshot | Withdrawn; intermittent PPS recovery unresolved | Disabled |

These are independent acceptance decisions. Neither may be enabled simply
because the other succeeds. The additional patch separates their root
evidence-admission paths: shutdown arming/admission uses successful capture and
actual previous-boot shutdown evidence; restore admission requires matching
successful capture/restore evidence. Each preserves the other's independently
admitted result for the exact receiver. Power-hold validation happens before any
qualification write. These code changes are not installed, and no live
qualification flags were changed.

## Additional recovery check under review

The existing snapshot/restore worker waits for a fresh paired-timing predicate,
but does not independently observe a new kernel PPS sequence after handover.
The additional patch takes a post-handover sequence baseline and requires a new
event, fresh validated UTC and paired/selected synchronized F10P before declaring
success. It reports the exact unmet stage within the existing 120-second bound.
Cancellation still propagates through ordinary cleanup; there is no reset retry.

Reports retain separate UTC, new-PPS and F10P recovery times measured **after
GPSD handover**, distinct from receiver TTFF or the timestamp of a reset. Failed
recovery retains any measured partial durations. Public JSON/CSV/text exports
whitelist only finite durations in bounds; missing observations remain unknown.
Advanced exposes the values in an expandable panel.

This is a stricter success criterion and diagnostic improvement, not a fix for
the physical PPS failure. It is not installed with the earlier safety overlay.

For later root qualification, an evidence file can explicitly select
`"capability": "shutdown"` or `"capability": "database_restore"`. The existing
shutdown-arm command defaults to shutdown; the record command defaults to
restore. Arming alone does not qualify shutdown or restoration. The existing
exact identity, completed-job, same-backup restore, timing recovery, previous
boot and power-interval requirements remain. No public/API qualification switch
is added.

## Manufacturer review and unresolved causes

The authorities reviewed are [F10 SPG 6.00, protocol 40.00 R02](https://content.u-blox.com/sites/default/files/documents/u-blox-F10-SPG-6.00_InterfaceDescription_UBX-23002975.pdf)
and [NEO-F10N Integration Manual R04](https://content.u-blox.com/sites/default/files/documents/NEO-F10N_IntegrationManual_UBXDOC-963802114-12193.pdf).
MGA-DBD payload contents are firmware-specific and intended for the originating
receiver. Completion supplies the number of dump entries; restoration ACKs must
match the offered entry. The documentation does not explain status 255.
TIM-TP describes the next scheduled output pulse and cannot alone prove that an
electrical edge reached GPIO26. No PPS failure category is assigned yet.

The retained-data restore rejection and successful cold-start restore occurred
under different conditions. Neither database capacity, Autonomous conflict,
transfer pacing nor a firmware defect has been established as the cause. Unknown
255 remains a rejection. Do not silently clear navigation data to make a restore
work, replay the complete archive indefinitely, or reinterpret it as success.

## Prepared supervised campaign

Separate operator authorization is pending for the disruptive campaign. No new
snapshot, restoration, reset or shutdown experiment has been performed for this
investigation yet. Root-private instrumentation is staged under
`/var/lib/pcs-gnss-qualification/reliability-20261117`, with a pinned complete
159-entry same-receiver backup. Its original integrity/identity/age metadata and
payload are kept intact and are not included in public exports.

The trial harness uses the existing `Owner` and receiver journal. It observes
kernel PPS and the validator lease independently, records monotonic receiver
stop/start and temporary configuration-write phases, polls navigation/TIM-TP
within exclusive ownership, and compares all relevant TIMEPULSE, Autonomous,
interference and ACKAIDING readback before/after. After returning the UART it
reads only GPSD's loopback raw feed. Full command/ACK/frame evidence remains
private, including the exact rejected entry. It opens no competing UART.

Each transient trial has a 250-second systemd start limit, bounded cancellation,
30-second stop limit and the established acquisition recovery StopPost. Receiver
resets are never an automatic recovery response. An incomplete timing recovery
stops the campaign for evidence review while ordinary NTP fallback remains.

Planned sequence:

1. Five comparable snapshots using the existing stop/dump/start sequence, with
   duration, completion count, UTC, new PPS, F10P and intervention recorded.
2. If justified, compare a separately identified receiver-running dump candidate.
   The DBD poll layout itself does not require a stop; hardware qualification is
   still necessary before changing the product sequence.
3. Compare pinned-database restoration over retained navigation, following an
   explicitly authorized software cold start, and using the early-start ordering.
   Repeat only a supported candidate; preserve matched ACKs and stop at failure.
4. If pulses disappear, compare TIM-TP, configuration, kernel sequence and
   admission/Chrony timelines before any supervised recovery reset. Direct
   electrical measurement may still be needed to distinguish receiver output
   from wiring/kernel failure; a serial pulse prediction is insufficient.
5. Exercise missing/malformed ACKs, timeout and cancellation through controlled
   fault injection first. Reuse existing interruption recovery evidence rather
   than causing unnecessary additional live faults.
6. After repeatable supported operation, qualify startup restore and shutdown
   saving independently. A real orderly shutdown trial requires its own approval
   and renewed confirmation of the power interval. No automatic cold reset is
   permitted to satisfy a boot policy.

## Validation

The deployed safety commit reran 1,188 ARM64 Python tests in 99.166 seconds,
with four expected skips and no failures. An initial run exposed Windows CRLF in
an unchanged shell configuration fixture; normalization was byte-compared with
the deployed Linux Git version before the successful repeat. The manifest
payload did not change. Nineteen frontend JavaScript tests passed.

The additional recovery and qualification checks ran 1,196 ARM64 Python tests in
98.781 seconds, with four expected skips and no failures. The Windows GNSS subset
ran 141 tests with six expected platform skips and no failures; all twenty safety fixtures
passed independently. Tests cover a cached paired status without new pulses,
invalid UTC despite advancing pulses, missing Chrony pairing, cancellation,
bounded/redacted recovery exports and the existing malformed ACK, timeout,
identity, ownership, partial transfer and recovery cases. Native source tests
ran unprivileged with devices hidden, without installed-code changes.
Qualification fixtures separately cover capture-only shutdown arming/admission,
preserved independent restore/shutdown results, and zero writes for an invalid
power budget. Their enrollment is supplied by the harness so the suite remains
unprivileged; production root ownership checks are unchanged.

Synthetic screenshots of expanded recovery details were inspected at phone,
tablet and desktop widths, with no horizontal page overflow. Fixture actions
could not reach the appliance. Fixture timing values are not hardware evidence.

## Rollback and support escalation

First finish or cancel maintenance and verify receiver journals/leases are
settled. Use the authorized exact-manifest rollback with the newest installer
receipt above, then verify sole GPSD ownership, fresh UTC, advancing PPS and
paired F10P. Restart only the web renderer when reverting its loaded code. Keep
credentials, valid assistance, navigation backups, history and withdrawn
qualification. The checked receipt chain preserves the earlier rollback layers.

If the receiver response remains unexplained, prepare a sanitized support case
with firmware/protocol, procedure, entry number, ACK type/status and comparative
results. Keep raw navigation payloads, exact receiver identity, coordinates and
credentials private. Sending a case or any private attachment to u-blox requires
an explicit operator instruction; none has been sent.
