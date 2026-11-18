# GNSS acquisition follow-up to PCS v2.5.1

For the subsequently deployed reliability corrections and current hardware
acceptance, see [the maintenance qualification report](gnss-maintenance-reliability.md).
That report supersedes the original completion-sampling and restoration
acceptance descriptions below. Both unattended database features remain held;
even software cold restoration can reject a valid snapshot.

This additive patch audits the requested acquisition system against the already
released implementation. It preserves the five-section GNSS interface, SBAS,
spectrum, GPSD ownership, F10N/F10P validity gate and existing AssistNow account,
cache, scheduler and quotas. It adds no services or receiver settings.

## Existing implementation and acceptance boundaries

| Requested capability | Implementation | Current acceptance |
| --- | --- | --- |
| Hot, warm and software cold starts | `pcs_gnss_acquisition.py` reset masks 0/1/FFFF, controlled mode 02; maintenance worker | Real starts measured; not electrical power cycles |
| Six assistance choices and five alternating A/B comparisons | Advanced Acquisition Testing and temporary experiment policy | Cached/no-external comparisons measured; fresh-versus-cached comparison incomplete |
| Separate download, TTFF, host fix, UTC, PPS and F10P metrics | Event observer, bounded private history, JSON/CSV/text export | Real measurements in the extended qualification report; missing values remain unknown |
| Cancellation, exclusive handover and interruption recovery | Existing admission identity checks, gate lease, GPSD control release and systemd recovery | Live cancellation and interrupted-worker recovery exercised |
| Normal boot/fallback policies | AssistNow settings and existing admission hook; asynchronous downloader | Policy file remains absent on commissioned hardware, retaining cache-only admission |
| Pi-hosted navigation snapshots and same-receiver restoration | Root-private atomic `NavigationStore`, complete-count/checksum checks and per-entry ACKs | Capture observed; restoration over retained data rejects an entry; automation held |
| Clean-shutdown snapshots | Bounded existing shutdown hook, separate hardware qualification and power-hold checks | Failure timeout/recovery exercised; unattended saving remains off |
| Predictive and optional Live | Existing Thingstream client and durable quota accounting | Predictive active; Live disabled, no paid requests |

See [the operator guide](gnss-page-guide.md), [ownership, protocol and recovery
details](gnss-acquisition.md), and [actual extended hardware evidence](v2.5.0-extended-qualification.md).

## Changes in this patch

Boot admission checks database restoration qualification before sending coarse
time or database frames. Missing or invalid prepared assistance returns a fixed
ordinary-acquisition status without starting a receiver transaction. Interrupted
boot-stop journals still require exact identity and recovery; the optional-cache
fallback does not swallow those recovery errors.

Database transfer failures now retain offered, accepted, rejected and
unacknowledged counts, the one-based failed entry, and a status code only from an
exactly matched ACK. Missing/malformed ACKs remain distinct from rejection.
Unknown codes fail without retry or invented interpretation. Cancellation still
propagates and temporary ACK configuration is restored through the existing
transaction journal.

The worker preserves the original failure phase through cleanup. Reports record
UART ownership recovery separately from paired timing sampled at completion.
The latter may be false before eventual normal reacquisition; it is not a claim
that PPS can never recover. Numeric evidence is explicitly whitelisted in public
JSON/CSV/text exports. Binary databases, identity bindings, digests, credentials
and private qualification reasons are excluded.

AssistNow shows an exact-receiver qualification-hold card. Advanced shows the
failure, recovery observations and expandable database transfer evidence. Existing
administrator sessions, confirmation forms and CSRF protections are unchanged.

## Manufacturer compatibility and unresolved hardware issues

The authorities remain [F10 SPG 6.00 protocol 40.00 R02](https://content.u-blox.com/sites/default/files/documents/u-blox-F10-SPG-6.00_InterfaceDescription_UBX-23002975.pdf)
and [NEO-F10N Integration Manual R04](https://content.u-blox.com/sites/default/files/documents/NEO-F10N_IntegrationManual_UBXDOC-963802114-12193.pdf).
The integration manual describes database restoration after restart, requires
care with Autonomous generation before shutdown, and recommends coarse UTC
aiding for restored Autonomous data. Those statements do not establish the cause
of the retained-data rejection or PPS recovery failure observed on PCS.

The real same-receiver 159-entry restore over retained navigation was rejected
at entry 53 with matched ACK status 255. This code remains undocumented and
unclassified. A separate cold-start boot restoration accepted all entries under
different starting conditions; it does not qualify the failing workflow.
A real complete snapshot also once recovered valid receiver UTC without kernel
PPS returning within 120 seconds. Ordinary supervised recovery restored F10P.
This patch improves containment and evidence; it does not resolve either cause.

Database restoration and shutdown qualification must remain withdrawn until
repeatable same-backup capture/restore, UTC/PPS/F10P recovery, cancellation,
shutdown and bounded-failure trials pass on this exact receiver. Keep the existing
power-hold and remote recovery precautions. No electrical power loss or absolute
PPS accuracy claim follows from software cold-start results.

## Validation and deployment

The final source suite runs unprivileged on ARM64 with device access hidden and
an empty GNSS runtime bind. It changes no installed code, production data,
service settings, receiver state or network policy. Twelve focused safety tests
cover qualification-before-write, unavailable cache fallback, interrupted-journal
identity rejection, partial ACKs, unknown status, timeout privacy, cancellation,
worker evidence, redacted export and qualification-hold presentation.

Synthetic browser fixtures exercise AssistNow and expanded Advanced evidence at
360, 768 and 1440 pixels. Screenshots are visually inspected; the page fits each
viewport without horizontal overflow. The fixture has no POST implementation or
connection to live PCS actions. These checks are interface evidence, not hardware
qualification. Final ARM64 validation ran 1,188 tests in 100.704 seconds with
four expected skips and no failures. The Windows GNSS subset ran 133 tests with
six expected platform skips and no failures. The final twelve safety fixtures
also passed independently. `git diff --check` passed.

A subsequent read-only appliance check confirmed the clean deployed v2.5.1
checkout and unchanged installed acquisition modules, GPSD sole ownership,
advancing kernel PPS, paired/selected F10P, fresh diagnostics, active normal
refresh and no failed services. The database automation hold remained intact.
This follow-up was not installed or released during these checks.

The executable changes are limited to `scripts/pcs_gnss_maintenance.py`,
`scripts/pcs_gnss_acquisition.py` and `web/pcs-control-panel/pcs_control_panel.py`.
New tests are in `tests/test_gnss_acquisition_safety.py`; documentation changes are
this audit, the page guide and changelog. There are no new dependencies, services,
downloads, background polling loops, flash writes or persistent receiver changes.
Additional stored evidence is a few bounded scalar fields per existing job.
CPU/memory impact has not been separately benchmarked.

For an authorized deployment, use the existing exact-manifest installer and a
fresh private rollback receipt. Preserve credentials, cache, quota state,
navigation objects, history, absent policy file and withdrawn qualification.
Do not run maintenance concurrently with installation or rollback. Verify exact
installed hashes, sole GPSD ownership, valid UTC, advancing kernel PPS and paired
F10P afterward. Restoring the prior manifest reverses this patch; do not reset
the receiver or reinstate qualification flags as part of rollback.
