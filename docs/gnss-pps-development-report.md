# NEO-F10N/PPS development report

Date: 2026-11-06. Baseline: PCS v2.3.2 / current main
`7873faaec908c10e5d86e824e8097d6068561c2f`.
Development branch: `codex/gnss-pps-v232`, isolated worktree `pcs-gnss-pps`.

## Completed software

- Optional, default-legacy configuration and verified USB enrollment. Generic
  CH340 identity is insufficient; serial-less devices require an enrolled USB
  topology. Model/protocol are polled exclusively before GPSD owns the port.
- Documented UBX configuration-database support, ACK/readback verification,
  original RAM snapshots and differential RAM initialization. No flash/BBR write,
  reset, GPS L5 override or Pi-UART use is performed automatically.
- GPSD UTC validator and private Chrony SOCK serial reference F10N. Advancing,
  fresh NAV-PVT/TOFF epochs, validity flags, GPS-week continuity and bounded
  uncertainty are required. Original GPSD timestamps are preserved.
- GPIO26 kernel-PPS identity resolution and F10P `lock F10N maxlockage 2` pairing.
  Refclocks start noselect; invalidation, daemon exit and watchdog failure withdraw
  them. Explicit reselection prevents Chrony retaining a just-withdrawn source.
  USB GNSS time precedes network/local fallback when Chrony accepts it.
- Separate passive preflight/plan, inactive versioned staging, guarded activation,
  backups, transaction locking and rollback. Boot configuration and package
  upgrades are outside activation. Changed/ambiguous legacy serial identity
  prevents automatic GPSD restoration; network/local timing remains available.
- Existing dashboard/status/self-test, LCD, matrix and RGB integration, with
  separate position, UTC, pulse and clock states. Manual location does not hide
  degraded timing. Public receiver data uses explicit scalar allowlists and
  hides dedicated-receiver coordinates, raw reports and USB identifiers.
- Existing 2947 receiver, 2948 selected-position and trusted-LAN sharing contracts
  preserved. WWAN initialization is skipped in dedicated mode; GNSS selection
  introduces no ModemManager, LTE, routing or RF operation.
- [Architecture and optional installation](gnss-pps.md), updated GPIO/time/location
  documentation and a [four-phase commissioning runbook](gnss-pps-commissioning.md),
  including manufacturer-based GPSSEL authentication, support checks, rollback
  and a separate L5 health-policy opt-in.

Implementation commits before this report: `d766bcf` (UTC/PPS policy and real
protocol fixtures), `0d2bd2b` (optional installation/recovery), and `6f3cdc9`
(existing monitoring and consumer surfaces). No GitHub publication was performed.

## Validation

Tests used synthetic receiver metadata, UBX/NMEA, PPS device-tree/events and
Chrony states. A disposable Debian 13 x86-64 VM provided GPSD 3.25, Chrony 4.6.1,
Python 3.13.5, real libgps/nginx and private network namespaces. It had no PCS
hardware or appliance connection. Chrony protocol fixtures used `-x`, so they
could not adjust the host clock; their NTP serving port was disabled.

| Check | Result |
| --- | --- |
| Full Linux Python regression | 1,020 tests run; 1,016 passed, four expected skips |
| New GNSS policy/installation/status tests | 42 passed on Linux |
| Real GPSD/Chrony protocol tests | Two passed; device-scoped raw UBX/TOFF, persistent alias reporting, named PPS pairing, selection and withdrawal |
| Portal JavaScript | 19 passed |
| Native Windows witness suites | 15 LAN + 18 Windows tests passed |
| Real SNMP fixture | PASS: isolated GET/WALK, optional objects, private credentials and bounded loss |
| VLAN namespaces | PASS: IPv4/IPv6 isolation and routed-client behavior |
| Legacy and VLAN bandwidth namespaces | Both PASS: caps, bypass, accounting, idempotence, cleanup and collisions |
| Starlink runtime fixture | PASS: missing-D-Bus regression, repair and repeat provisioning |
| NetworkManager keyfiles | PASS: staged parent/LAN/WAN profiles verified by libnm without daemon/network changes |
| Recovery fixture | Eight scenarios passed, including repeated staging/rollback; no physical recovery qualification |
| Inactive software staging | Repeated staging produced the same bundle; GPSD/Chrony configuration hashes and active/enablement states were unchanged |
| Static checks | Python compilation, all shell syntax, new systemd unit verification and Git whitespace checks passed |

The new tests cover legacy default behavior; positive/ambiguous USB identification;
renumbering, absence and recovery; valid/invalid UTC, fix and PPS states; repeated,
stale and discontinuous epochs; source rejection, network time and local holdover;
wrong-source pairing; LTE-command isolation; Manual/APRS/Meshtastic compatibility;
private projections; RAM readback failure; transaction recovery and idempotency.
Optional EM7565 AUX failure remains outside NEO source ownership and cannot
trigger LTE reconfiguration or automatic receiver selection.

Initial VM runs exposed missing test packages, distribution AppArmor restrictions
on temporary protocol fixtures and existing tests' unprivileged-user assumptions.
These were resolved in the disposable environment. Protocol fixtures copy the
exact installed binaries outside their distribution AppArmor attachment paths;
that verifies protocols, not production confinement or hardware activation.
No outstanding failure is being accepted as a passing test. Expected skips are
the four native Windows Winsock cases, which were separately exercised in the
passing Windows suite; they are not deferred hardware acceptance.

Local evidence is retained outside the repository under
`tmp/gnss-development/validation/`: full/focused Python logs, JavaScript, SNMP,
VLAN, both bandwidth profiles, Starlink, keyfile and recovery logs. Those local
logs and the disposable VM are not release artifacts or CI results.

## Acceptance still required

- Delivered receiver revision, MON-VER protocol, CH340 identifiers/topology,
  exclusive serial ownership and package versions on the actual Pi. Initial
  support is protocol 40.00 at verified 38400 8N1, GPSD >=3.25 and Chrony >=4.6;
  other combinations require qualification and are not silently upgraded.
- GPIO26 physical availability, both power-up orders, no SAFEBOOT loading,
  antenna electrical compatibility, actual PPS voltage/edge and kernel events.
- Measured serial latency, UTC/PPS pairing, actual timing uncertainty/accuracy,
  rejection, USB/network/local fallback, loss/recovery and clock continuity.
- Real staged activation, systemd/AppArmor behavior, cold boot/shutdown, rollback,
  all PCS consumers and public redaction on the commissioned appliance.
- Exact EM7565 firmware, baseline GPSSEL readback, GPSSEL=1 support and documented
  activation/reset requirements. The accessible manufacturer reference does not
  establish installed-firmware activation semantics. Do not write GPSSEL until
  this is resolved; any reset requires separate approval.
- Actual AUX satellite reception, LTE stability/throughput/diversity/MIMO/CA and
  GPSSEL rollback. AUX remains an unqualified GNSS path, not a reliable backup.
- Actual GPS L5 availability/health policy, any separately approved persistent
  settings/restart, cassette versus external antenna and extension performance.

No live appliance access, deployment, source switch, modem command/reset,
ModemManager restart, boot edit, appliance reboot or RF transmission occurred.
Software tests establish development behavior; they do not establish PPS
accuracy, RF reception, hardware compatibility or field commissioning.
