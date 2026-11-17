# GNSS acquisition testing and navigation preservation

The v2.5.0 acquisition extension uses the existing five-section GNSS page.
Advanced contains confirmed, disruptive acquisition tests; AssistNow contains
normal boot policy and navigation-snapshot controls. The Overview, satellite,
RF, SBAS and timing displays retain their existing measurement contracts.

## Operator controls

Advanced → Acquisition Testing offers hot, warm and software cold starts,
one to five repetitions, five alternating A/B comparisons, a per-trial
15–300 second acquisition timeout, source/fallback choices and a private antenna
note. Review shows the selected request and timing-interruption warning before
the final administrator/CSRF-protected confirmation. Progress refreshes every
five seconds while running. Cancel and Recover requests bounded cleanup.
History retains twenty operations, with JSON, CSV and readable text exports.

No-external-assistance trials perform no download or host injection. They are
not claimed fully unassisted: receiver Autonomous and flash data may survive.
Warm/hot comparisons retain navigation from earlier trials; the report flags
this carry-over. A software cold reset is not an electrical power cycle.
Keep the antenna stationary and review RF conditions before interpreting A/B
results. Faster acquisition does not establish better absolute PPS accuracy.

Each report separates download time, assistance injection/acceptance, receiver
TTFF, host-observed first 2D/3D fix, valid receiver UTC, advancing kernel PPS,
Chrony synchronization and F10P selection. Host fix events are recorded after
NAV-STATUS confirms a new receiver session; this observation barrier can add up
to a navigation epoch. Missing measurements remain null. A failed or cancelled
trial does not contribute successful TTFF statistics. Fallback results are not
pooled with the originally requested assistance source. No simulated numbers
are accepted as hardware qualification.

## Receiver protocol and ownership

The manufacturer authority is [F10 SPG 6.00, protocol 40.00, UBX-23002975 R02](https://content.u-blox.com/sites/default/files/documents/u-blox-F10-SPG-6.00_InterfaceDescription_UBX-23002975.pdf).
CFG-RST uses masks `0x0000`, `0x0001`, `0xFFFF` and controlled GNSS-only reset
mode `0x02`. No required reset ACK is assumed. New-session telemetry is the
verification barrier. Configuration, baud, TIMEPULSE and constellation settings
are preserved; this feature does not write flash.

A root-private job is queued through the existing fixed root helper. The worker
first prepares downloads without receiver/Chrony locks. It then obtains the
existing installation lock, requests the timing gate's maintenance lease, and
waits for an acknowledgement written **after** F10N/F10P withdrawal. GPSD's
control socket releases only the enrolled alias from the same read-only GPSD
process. The existing admission helper checks enrollment, exclusive ownership,
115200 baud and exact MON-VER/SEC-UNIQID identity before any command.

The restricted UTC gate retains its existing capability set. A root-owned
runtime admission receipt binds the device generation to the GPSD process birth;
this lets a gate update recognize the existing verified owner without ptrace
privileges or a GPSD restart. Deployment uses the root-only `record-owner` helper
after verifying the sole read-only owner. New admissions replace that receipt
only after successful exclusive receiver preparation. PID reuse, a changed
device generation, writable/non-root receipts and stale admission timing fail
the ownership check.

The gate remains withdrawn for the entire lease, including malformed or stale
leases. It does not restart GPSD or Chrony. Cleanup restores temporary RAM
ACK policy, starts a deliberately stopped GNSS engine, closes the exclusive
descriptor, verifies GPSD alone regained ownership, and only then releases the
lease. Fresh UTC/TOFF/PPS evidence is required afterward. Process interruption
uses systemd StopPost recovery. Recovery failure leaves GNSS references withdrawn
and reports Recovery required; Internet NTP/holdover continues under existing
Chrony configuration. Reboot admission recovers a prior-boot journal only after
exact identity checks. A same-boot GPSD generation change requires operator review.

## Assistance policies

The new policy file `/etc/pcs/gnss-acquisition.json` is absent on installation,
preserving the previously commissioned injection behavior and all existing
Thingstream credentials, quotas, metered permission and refresh scheduling.
Saving a policy is an explicit operator action; it never resets or immediately
injects into a healthy receiver.

| Policy | Startup behavior |
| --- | --- |
| Automatic | Qualified same-receiver database, then compatible current-day cache, then retained data |
| Receiver-retained only | No host injection |
| Saved receiver database | Latest complete, compatible, age-valid qualified snapshot |
| Cached Predictive Orbits | GPS/Galileo current-day MGA-ANO only |
| Internet Predictive Orbits | Asynchronous bounded attempt; use only an early-ready response or selected fallback |
| Live Orbits | Explicit existing entitlement/cost activation; bounded asynchronous attempt |
| No host assistance | Ordinary acquisition without host injection |

Local, retained-only and no-fallback policies are separate choices. An experiment
uses a temporary in-memory override and never changes normal boot settings.
Fresh Internet/Live experiments download once and share that response across
repetitions; existing duplicate suppression, backoff and account limits still
apply. A suppressed request is not called a fresh download. No modem connection,
uplink change or tunnel is initiated. Normal refresh defers while an experiment
is active, including an explicitly unassisted experiment. Late boot downloads
update the future cache; they do not reset or reinject into healthy timing.

Coarse MGA-INI-TIME_UTC aiding uses independently bounded approximate UTC, with
unknown leap seconds and trustedSource clear. It is never a Chrony sample.
The date may come from selected Internet NTP, two fresh agreeing public Internet
NTP measurements already held by Chrony, or a DS1307 cross-checked against such
time within seven days. RTC age/drift bounds are conservative; missing,
backward, implausible or unvalidated RTC data is rejected. The existing strict
receiver UTC/PPS gate remains authoritative. No position aiding or position
transmission is enabled. Additional almanac/position optimization is unqualified.

## Navigation database

MGA-DBD is firmware-specific, opaque receiver data, not the seven-day downloaded
orbit archive and not UPD-SOS receiver-flash backup. A confirmed manual snapshot
checks Autonomous activity, stops the GNSS engine with controlled mode `0x08`,
collects validated DBD entries, and requires an MGA-ACK completion carrying the
exact entry count. Cleanup starts GNSS with mode `0x09`. Restoration requires
per-entry matching successful acknowledgements; any rejection fails qualification.
F10 SPG 6.00 also requires ACKAIDING for dump-completion acknowledgement;
capture temporarily enables it in RAM and restores its journaled original value.

Objects live under root-owned mode-0700 `/var/lib/pcs-gnss-acquisition/navigation`;
payloads and metadata are mode 0600. Immutable payload and metadata publication
precedes the atomic latest pointer. Failed/incomplete writes retain the last
valid backup. Exact receiver/firmware identity, framing/checksum, SHA-256,
completion count and age are revalidated on restore. Default age is 24 hours,
retention five successful backups (maximum ten, each at most 1 MiB). A 12 MiB
total storage cap also bounds orphan objects after failed publication; reaching
it refuses a new save while retaining the last complete backup.
Exports omit dumps, identifiers, coordinates and operator notes.

Automatic restore and shutdown saving require hardware evidence admitted by
the root-only qualification command. Manual capture and restoration remain
explicit, confirmed supervised maintenance operations for commissioning.
Periodic snapshots remain disabled because repeated timing interruptions have
not been qualified. Shutdown saving defaults off. USB power loss without a
clean shutdown cannot produce a snapshot. No retained-power interval is assumed.

## Hardware commissioning

Deployment authorization does not itself authorize a live receiver reset,
benchmark, boot-policy change or shutdown experiment. Obtain explicit operator
authorization for the following disruptive sequence, and retain the private
reports outside Git:

1. Confirm enrollment, UTC/PPS pairing, existing cache, network limits and NTP
   fallback; keep the antenna unchanged.
2. Save a manual snapshot, restore it to the same F10N and verify all DBD ACKs,
   recovered UTC, advancing kernel PPS and F10P selection.
3. Run one hot, warm and cold experiment, then cold no-external versus cached
   Predictive A/B. Record real TTFF and recovery measurements, residual-data
   caveats and acknowledgement/status evidence of accepted assistance.
4. Confirm independently bounded RTC/time aiding after cold navigation clear.
5. Cancel a test and verify ordinary operation. Exercise interrupted ownership
   recovery, including a failed injection, without bypassing identity checks.
6. Create a root-owned mode-0600 evidence JSON file containing successful
   `snapshot` and `restore` history IDs for the same database, plus the verified
   `power_hold_seconds` (at least 90) for a separate shutdown qualification.

```sh
sudo pcs-gnss-info qualification-record --evidence-file /root/pcs-gnss-evidence.json \
  --maintenance-confirm 'I authorize GNSS timing maintenance'
```

For an explicitly authorized orderly shutdown trial, replace `qualification-record`
with `qualification-arm-shutdown`. This arms one snapshot, expires after fifteen
minutes, and is consumed before I/O. It does not shut PCS down. After a separately
authorized clean shutdown and restart, run `qualification-record` again. Shutdown
qualification requires a completed same-receiver snapshot from the previous boot.
Do not mark a failed/unsupported dump or restore as qualified. Test that backup
failure never blocks shutdown beyond systemd's bounded stop time, and repeat the
PCS self-test after reboot. Only then enable normal shutdown saving if desired.

## Files, services, overhead and rollback

New modules: `scripts/pcs_gnss_acquisition.py`, `scripts/pcs_gnss_maintenance.py`.
Existing admission, UTC gate, GNSS root helper, web renderer and additive installer
are extended. New units are `pcs-gnss-acquisition.service`, its fixed recovery unit,
`pcs-gnss-navigation-shutdown.service` and `pcs-assistnow-boot-refresh.service`.
The worker is on demand, max ten trials and eighty minutes per job, with bounded
per-phase I/O and stop cleanup. Shutdown collection has a 10–30 second budget,
plus bounded cleanup; systemd stop timeout is 55 seconds, followed by bounded
StopPost recovery if forced termination is needed. A conservative 90-second
power-hold budget covers both paths. No new idle serial or
network polling daemon is introduced. History is bounded to twenty small JSON
reports; navigation storage is at most ten successful 1 MiB objects plus temporary
publication objects reclaimed after successful saves. Existing Predictive/Live
daily/monthly/data quotas remain unchanged. Measure CPU/RSS on hardware during
qualification; no benchmark estimate is a measured acceptance result.

Stage/install using the [additive installer](gnss-intelligence-install.md). Its
expanded exact manifest also backs up the UTC gate. It never starts services or
creates an active acquisition policy. After authorized activation, restart the
UTC gate and web backend once; do not restart GPSD/Chrony for routine operations.
Enable the shutdown and boot-refresh units only as part of reviewed deployment.
The shutdown unit starts with a no-op and does no backup unless enabled/qualified
or separately armed. Reverse shutdown ordering keeps GPSD, Chrony and the gate
alive until its snapshot/cleanup completes.

Before rollback, cancel/recover active maintenance, confirm GPSD ownership and
paired UTC/PPS, and disable the two optional boot/shutdown units. Roll back the
exact saved manifest using the installer receipt, then restart the restored gate,
web and passive-info services. The installer refuses pending receiver journals,
unreleased leases and subsequent operator edits. It accepts earlier fifteen-file
GNSS receipts for historical recovery. Credentials/cache and private test history
are retained. Software rollback never substitutes for receiver recovery.
