# GNSS Information: data and settings

Open **Administration → GNSS Information** (`/admin/gnss`). The five sections
share existing administrator authentication. Measurements are read-only; action
forms use existing CSRF protection. Disruptive maintenance has a separate review
screen and explicit timing-interruption confirmation. Expanding details does
not perform maintenance. Missing, unsupported, disabled and stale are different.

## Overview

Six summary cards answer the immediate questions: fix/DGPS state, unique
spacecraft used/visible, horizontal positioning accuracy, Chrony/PPS selection,
actual correction source/health and Predictive cache readiness.

Navigation adds acquisition state, altitude MSL, receiver TTFF and measurement
freshness. Corrections show whether differential corrections are actually applied,
the reported system/source, selected SBAS PRN, correction/integrity services and
telemetry age. WAAS is displayed only when reported; reception alone does not
prove a WAAS-corrected fix. An unavailable source stays Unknown. NAV-SBAS does
not report a correction age, so the page does not invent one.

Time synchronization separates valid receiver UTC, kernel PPS availability and
activity, paired PPS state, selected Chrony reference, measured source residuals
and system offset. Position accuracy in metres, receiver time accuracy in
nanoseconds and measured Chrony offset are different measurements. PPS alone
does not establish valid UTC or prove absolute clock accuracy.

## Satellites

The sky plot shows measured elevation/azimuth where present. Signal charts show
actual dB-Hz for L1/L5, with GPS/Galileo/BeiDou breakdowns. Filters select GNSS,
band, used-only and search text. Detailed rows are expandable/contained on phones.
Rows retain tracking quality, navigation use, health and observation freshness.
Counts consolidate multiple frequencies/signals belonging to one spacecraft;
they are not inflated by counting L1 and L5 twice. Trends use observed points.

## RF Diagnostics

RF paths report measured AGC, receiver interference/jamming indicators,
spoofing state, availability and age. Unknown, Not Supported, Disabled,
No Indication, Warning and Critical remain distinct. No spoofing indication
is not proof of authentic signals.

The spectrum uses actual MON-SPAN bins/scales, reported centre/bandwidth and
capture time. Select L1, L5 or comparison and current/peak/average traces.
Only one primary view appears at a time on mobile. This is a receiver diagnostic,
not a calibrated spectrum analyser.

Start Capture retains a bounded passive collection; Stop ends it, Snapshot saves
a bounded local diagnostic object, and history shows deduplicated timestamped RF
events. Captures expire automatically. These controls do not transmit, change
hardware power, reset GNSS or compete for the UART. Missing live spectrum cannot
be enabled by silently interrupting GPSD; message-rate changes require qualified
maintenance. Redacted export is in Advanced.

## AssistNow

The cache card shows presence/expiration, available prediction dates and supported
constellations. Download status shows last success, selected existing uplink,
next refresh, bytes/request usage and last injection outcome. Autonomous state
comes from receiver configuration and orbit/activity reports; internal retained
data cannot be assumed to survive complete power loss.

| Setting/action | Meaning |
| --- | --- |
| Refresh Predictive Now | Use an already active uplink; existing duplicate suppression, backoff and quota checks apply |
| Automatic Refresh | Enable/disable opportunistic scheduled cache refresh |
| Prediction Window | 1/3/7/14 service days, subject to receiver/service validation; deployed default 7 |
| Refresh Interval | Deployed 72 hours; does not wait for Internet at GNSS startup |
| Request/Data Limits | Durable Predictive/Live daily, monthly request and byte limits; deployed 1 MiB/day and two Predictive requests/day |
| Metered Uplink Permission | Existing operator authorization allows metered/unknown uplinks within limits |
| Live Orbits | Separately entitled and potentially chargeable; disabled, no cost authorization, no automatic enrollment |
| Clear Expired Cache | Remove expired payloads while preserving valid cache |
| Normal Boot Policy | Automatic, retained only, saved database, cached Predictive, bounded Internet Predictive, optional Live or no host assistance |
| Fallback Policy | Valid local assistance, retained-only, or none during controlled comparisons |
| Save Navigation on Shutdown | Default off; requires same-receiver hardware qualification and verified power interval |
| Restore on Next Eligible Start | One successful qualified database restoration; healthy fix/PPS still defers unnecessary injection |
| Database Age / Retention | Default 24 hours and five successful backups; configurable within validated bounds |
| Shutdown Budget / Power Hold | Bounded 10–30-second collection; verified power interval must cover collection and cleanup |
| Save Snapshot Now / Restore Database | Reviewed, disruptive exclusive maintenance; exact receiver, firmware, checksum, age and ACK checks |

The acquisition policy file remains absent after this deployment, so the active
boot behavior is the existing cache-only admission. Saving policy is a separate
operator choice. Database capture/restore/shutdown are qualified for this F10N;
normal shutdown saving remains off. Downloaded Predictive archives and Pi-hosted
receiver databases are separate stores, with separate dates and validity checks.

Autonomous, predictions and fresh ephemerides can coexist; they are not DGPS/SBAS
position corrections. Host predictions select compatible current-day GPS/Galileo
MGA-ANO messages. Unsupported BeiDou assistance is excluded. No fresh download
resets an already synchronized receiver, and network activity holds no UART lock.

## Advanced

Receiver/connection details show model, firmware, hardware, protocol, enrolled
alias, baud, USB/receiver connection and identity-verification time. Accuracy
details include horizontal/vertical estimates, ellipsoid/MSL altitude and DOP.
Timing details distinguish receiver time accuracy, PPS age, source residuals,
Chrony offset/uncertainty and freshness. Expandable diagnostics show decoded
messages, availability and rejection counts. SBAS details include actual
per-satellite correction reports, UDRE codes and application indicators.

Autonomous enable/disable requests are queued for separately authorized receiver
maintenance with readback. They do not silently write flash. Diagnostic exports
omit account credentials, chip identity, coordinates, altitude and satellite
geometry. Private navigation dumps are never included in public reports.

Acquisition Testing offers hot/warm/full software cold starts, six assistance
choices, five A/B comparisons, one to five repetitions, bounded timeouts and
explicit fallback. Test policy overrides are temporary and leave normal boot
settings intact. Paid Live requests need entitlement and per-experiment consent.
Cancel/Recover releases ownership safely; live progress and current UTC/PPS state
remain visible. Results/history export JSON, CSV and readable reports.

Reports separate download duration, delivery ACK counts/time, receiver TTFF,
host first 2D/3D, valid receiver UTC, advancing kernel PPS, Chrony synchronization
and F10P selection. Missing metrics remain unknown. Failed/cancelled trials do
not enter successful statistics. Median/fastest/slowest and observed A/B
differences retain warnings about RF variation and residual Autonomous/flash
data. A software cold start is not a power cycle. Never move the antenna between
comparison trials; a faster fix is not evidence of better absolute PPS accuracy.

See [measured v2.5.0 acceptance](v2.5.0-validation.md) for actual results and
remaining physical field gates, and [acquisition recovery](gnss-acquisition.md)
before changing normal policies or running disruptive maintenance.
