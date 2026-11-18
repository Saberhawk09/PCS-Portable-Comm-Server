# GNSS reliability release candidate

Candidate for the next separately approved PCS maintenance release through
[PR #112](https://github.com/Saberhawk09/PCS-Portable-Comm-Server/pull/112).
No existing release tag is changed by this candidate.

* Require fresh UTC, a new kernel PPS event, stable 1 Hz cadence and paired,
  selected F10P before navigation maintenance succeeds.
* Preserve the original failure/cancellation outcome, precise recovery stage and
  redacted ACK counts while performing bounded ownership/timing cleanup.
* Reject live database overlays and warm/hot database restore trials. Keep cold
  restoration explicitly experimental; some valid backups are rejected.
* Correct coarse-time delivery ordering and separate startup/shutdown
  qualification admission. Keep both unattended database features disabled.
* Preserve the commissioned UART, GPIO26 PPS, UTC trust gate, Predictive cache,
  quotas, normal boot policy and unrelated PCS services.

The reliability code is already installed as a verified overlay. Final closeout
changes add documentation only. Fresh ARM64 regression passed 1,201 tests (four
expected skips); Windows GNSS passed 215 (nine skips); frontend passed 19.
Actual power-up reached selected F10P 24.561 s after kernel boot. Real snapshot
and restoration trials, failure recovery and remaining gates are documented in
[the final acceptance report](gnss-final-closeout.md).

The historical PPS-only stall, stopped-interval event burst and MGA status 255
remain unexplained. This release improves containment; it does not claim those
hardware behaviors are fixed. Use ordinary timing and Predictive assistance in
the field, with database automation off. Physical loss/recovery, electrical PPS
isolation, prolonged field endurance and independent absolute timing measurements
remain deferred. Current SBAS runtime availability is also unresolved despite
enabled RAM/default settings. No paid service or unrelated modem/network change
is included.
