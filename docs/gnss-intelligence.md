# GNSS Information, RF diagnostics and AssistNow (v2.4.1 candidate)

This is an additive development candidate based on commissioned v2.4.0 commit
`55a1bd336c0dc50771480aa9afb78bedf2050f63`. Installing software, enabling passive
diagnostics, authorizing receiver maintenance, and accepting RF/timing hardware
behavior are separate operations. Nothing in this candidate deploys itself.

The authenticated page is **Administration → GNSS Information**, at
`/admin/gnss`. It reuses the existing Python backend, administrator sessions,
CSRF validation, styles and nginx `/admin/` proxy. Every control works without
JavaScript. HTTP reads consume local snapshots; they do not poll peripherals.
Redacted exports use an explicit field schema and omit credentials, chip identity,
coordinates, altitude and satellite azimuth/elevation geometry. Geometry remains
available on the authenticated page; its combination with known orbits can reveal
location and does not belong in a shareable redacted report.

## Ownership and timing

During normal operation GPSD remains the only physical UART owner. The new
`pcs-gnss-info.service` opens two GPSD loopback connections at `127.0.0.1:2947`,
both restricted to `/dev/pcs-gnss-neo`: binary `WATCH raw=2` and JSON SKY.
It has `PrivateDevices=yes`, an empty capability set, no systemd or Chrony
configuration command path and no network-management interface. Its failure cannot withdraw timing
sources, change GPIO, reset peripherals or initiate LTE. It does not use the
selected/manual position feed for timing or assistance.

The commissioned F10N/F10P validity gate, kernel GPIO26/pin37 PPS,
`lock F10N maxlockage 2`, UART1 115200, Internet NTP, RTC holdover and EM7565
GNSS-disabled configuration are preserved. No automatic receiver failover is
introduced. Existing GPSD `-b` read-only configuration is retained.

Receiver writes are restricted to two explicit contexts:

1. **Operator maintenance**, with GPSD service/socket and the PCS UTC gate
   already released by the operator. The manager checks their state, shares
   `/run/pcs-gnss/installation.lock` with admission/probe/activation, verifies
   USB topology/enrollment, then uses the existing `fuser`/TIOCEXCL serial
   implementation. It never stops or restarts those services itself.
2. **Opt-in boot cache injection**, inside the existing `prepare_receiver`
   exclusive file-descriptor scope before GPSD opens the UART. No additional
   serial process, Internet request or downloader lock is involved. Default is
   disabled. Healthy/fixed receivers are left alone. No late injection occurs.

Only `CFG-ANA-USE_ANA`, optional `CFG-ITFM-ENABLE`, and temporary
`CFG-NAVSPG-ACKAIDING` boolean RAM values are admitted by the manager. No baud,
timepulse, orbit-error threshold, flash, BBR persistence, reset, power or modem
command is admitted. Changes require readback. Temporary ACK policy has a
durable journal and is restored after success or failure. Admission recovers a
pending journal only after verifying the same chip/firmware identity; a mismatch
or unbound journal requires maintenance review without changing the new receiver.
Failure to restore requires operator attention rather than
silently accepting an unknown receiver state.

## Measurements and RF history

The page reports actual received messages. Unobserved data is Unknown; missing
messages are not evidence of lack of receiver support. An unsupported message
version is Not Supported. Detection Disabled, No Indication, Warning and Critical
retain separate meanings. No spoofing indication proves nothing about signal
authenticity. Receiver navigation status never replaces the authoritative PCS
UTC validity gate.

For per-reference PPS residuals it makes a fixed read-only `chronyc -h 127.0.0.1
-n sources` query every five seconds. The loopback monitoring protocol does not
authorize Chrony state changes. Only F10N/F10P offsets/reach/sample age are kept;
NTP addresses are discarded. Measured and slew-adjusted source offsets remain
distinct from Chrony's system-clock correction. Missing/unreachable sources
produce Unknown, and old source samples are marked stale. This does not measure
absolute pulse accuracy. See the [Chrony 4.6 manual](https://chrony-project.org/doc/4.6/chronyc.html).

Per-message measurement age is limited to five seconds. A stopped collector
makes all measurements visibly stale after ten seconds. The page retains stale
values for investigation with their freshness labels. Position uncertainty in
metres, receiver time accuracy in nanoseconds, physical PPS behavior and Chrony
system offset in seconds are displayed separately. TTFF comes only from a
nonzero NAV-STATUS `ttff`; a connection timestamp is not a TTFF estimate.

SKY spacecraft counts use v2.4.0 `SatelliteActivity`/`satellite_counts`, combining
L1/L5 rows by `(gnssid, svid)`. NAV-SIG rows retain individual signals; tracking
counts mean quality >= 2 (acquired, including unusable detections). Geometry is
joined only with fresh NAV-SAT data within two seconds of the signal epoch.
Signal-count history contains up to 120 actual samples, with no synthetic gap
filling. Unknown signal IDs are not assigned guessed bands.

RF/security state transitions and SEC-SIGLOG events are persisted atomically
under `/var/lib/pcs-assistnow/rf-events.json`, with a maximum of 256 events.
Repeated warnings do not generate a row for every poll. Receiver elapsed event
times are converted to explicitly estimated host timestamps, with a three-second
deduplication tolerance. Monotonic anchors prevent duplicates from host clock
steps while the process runs. Restart matching uses stored host time; a large
clock correction spanning a process restart can leave ambiguous event identities.
The receiver's >45-day sentinel remains an unknown occurrence timestamp.

Use event observations to compare with operator notes about LTE uploads,
Starlink, amateur radio or regulator loading. No diagnostic operation initiates
transmissions, changes power, or claims an RF cause from correlation alone.

## Spectrum capture

Normal capture records **already-output MON-SPAN only**. If MON-SPAN is not
fresh, Start Capture and Snapshot are disabled. GPSD read-only mode cannot
safely change output rates, so this candidate does not do that through GPSD.
Changing diagnostic message rates remains a separately approved maintenance
task; an absent message must not trigger a live UART takeover.

Duration is 5–300 seconds, default 60. A repeated Start cannot extend an active
session. Stop, deadline and process termination end capture. No temporary
receiver rate is changed, so there is no rate setting to restore.

MON-SPAN version 0 uses a four-byte header and 272-byte RF blocks: 256 amplitude
bytes, span/resolution/center in Hz, PGA in dB and reserved bytes. Amplitude is
byte/4 dB. Bin frequency is exactly `center + span * (i - 127) / 256`. PGA and
fixed/external LNA gains are not added to these values. L1/L5 classification uses
the reported center frequency; unknown centers stay Unknown. All plots contain
measured points. Comparison uses frequency offset from each path's own center.

Peak/mean traces retain at most 60 frames and combine only the same path,
frequency grid and PGA. Mean means arithmetic mean of measured dB values,
not a calibrated linear-power average. At most ten diagnostic snapshots are
kept locally. Capture timestamp is host receipt time: MON-SPAN has no receiver
UTC capture field. The instrument is comparative receiver diagnostics, not a
calibrated laboratory spectrum analyzer.

## AssistNow service compatibility and registration

Verified manufacturer references (consulted November 16, 2026):

- [F10 SPG 6.00 protocol 40.00, UBX-23002975 R02](https://content.u-blox.com/sites/default/files/documents/u-blox-F10-SPG-6.00_InterfaceDescription_UBX-23002975.pdf):
  sections 3.12, 3.13.6/8, 3.14.1/7/13/15/17, 3.16.1–3 and 4.9.1/8/12.
- [NEO-F10N Integration Manual R04](https://content.u-blox.com/sites/default/files/documents/NEO-F10N_IntegrationManual_UBXDOC-963802114-12193.pdf):
  sections 3.4, 3.11 and 4.2.3. Its explicit BeiDou B1I assistance warning is
  implemented as a rejection, not just a request preference.
- [Current Thingstream registration and request guide](https://support.thingstream.io/hc/en-gb/articles/20837860069788-AssistNow-Getting-Started-Guide),
  [API 1.0.4](https://assistnowapi.services.u-blox.com/) and
  [service specifications](https://support.thingstream.io/hc/en-gb/articles/19691890148892-AssistNow-service-specifications).
- [GPSD WATCH protocol](https://gpsd.gitlab.io/gpsd/gpsd_json.html).

The qualified software profile is **NEO-F10N, EXT SPGL1L5 6.00, protocol 40.00**.
Full MON-VER and SEC-UNIQID responses bind enrollment, cache and injections to
the verified chip identity plus firmware/hardware/protocol. Another receiver or
firmware requires review and re-enrollment. CH340 VID/PID alone is insufficient.

Commissioning found SEC-UNIQID version 2 (six ID bytes) on this F10N, whereas
F10 interface R02 lists version 1 (five bytes). PCS admits only these exact
version/length pairs and binds all ID bytes. The version-2 layout is documented
in the manufacturer's [M10 SPG 5.00 interface description](https://content.u-blox.com/sites/default/files/u-blox%20M10-SPG-5.00_InterfaceDescription_UBX-20053845.pdf)
and illustrated by the current [Thingstream integration guide](https://support.thingstream.io/hc/en-gb/articles/19691732434972-AssistNow-service-integration-guide).
This is an observed F10 documentation discrepancy, not a change of the admitted
receiver model, firmware or protocol. Other formats remain rejected.

Create a Thingstream domain/device profile yourself, choosing **OTHER** for this
module and **Predictive Orbits** for free-only enrollment. Account creation,
subscriptions and billable activation are never performed by PCS. Select the
evaluation/commercial profile appropriate to your account and verify its actual
terms in the portal. Published guidance currently lists 300 requests/month/device
for evaluation and 2 Predictive/20 Live requests/day/device for commercial; other
vendor migration guidance has listed a lower Live allowance. PCS conservatively
caps Live at 10/day, default 1, and all requests at 250/month, configurable down
to zero and up to 300/month. Verify account-specific limits before changing caps.

During a separately authorized maintenance session, obtain receiver facts with:

```sh
sudo pcs-gnss-info maintenance-inspect \
  --maintenance-confirm 'I authorize GNSS timing maintenance'
```

This refuses an active/activating GPSD service, socket or timing validator.
It does not release ownership for you. Follow the existing commissioned timing
maintenance/holdover procedure and resume the original services afterwards.
The inspect operation reads current Autonomous/ITFM settings without changing
them. Facts expire for enrollment after 24 hours.

Place `{"token":"YOUR_DEVICE_PROFILE_ZTP_TOKEN"}` in a root-owned mode-0600 JSON
file outside the checkout, such as `/etc/pcs/assistnow/ztp.json`, using an
appropriate private editor. Never put it in shell arguments or documentation.
After serial ownership has been released by the inspect operation, enroll once:

```sh
sudo pcs-gnss-info enroll --token-file /etc/pcs/assistnow/ztp.json \
  --maintenance-confirm 'I authorize GNSS timing maintenance'
```

Enrollment sends the token and full identity response frames to
`https://api.thingstream.io/ztp/assistnow/credentials`. Credentials go to
`/etc/pcs/assistnow/credentials.json` (root:root 0600); raw identity frames stay
private. No GNSS position is transmitted. PCS validates the returned service URL
and `allowedData` entitlement. Evaluation access cannot be inferred from
`allowedData` alone; its exact profile and remaining vendor quota must be checked
in the portal. The page explicitly reports that uncertainty.

The enrollment response parser accepts the guide's `allowedData` string array
and the comma-separated string observed from the live service. Both normalize
to a bounded list of exact entitlement names. Partial names, malformed CSV,
query parameters and alternate service endpoints cannot grant permissions;
Live still requires separate local enablement and cost consent.

## Cache, refresh and cost controls

The client uses verified HTTPS to the current `GetAssistNowData.ashx` endpoint,
with device-specific chipcode and `gnss=gps,gal`. It rejects redirects and ignores
proxy environment variables to avoid credential forwarding. Exceptions and
exports never contain the query URL, token or chipcode. Position-dependent
optimization is not implemented, so no precise position can be sent accidentally.

Predictive requests select `uporb_1`, `uporb_3`, `uporb_7` or `uporb_14`. Default
is seven days and a 72-hour refresh policy. Full UBX framing, length, checksum,
ANO type/version, dates and GPS/Galileo spacecraft ranges are validated. Other
constellations and non-ANO archive messages are rejected. No receiver flash is
used. A content-addressed payload is written and fsynced before atomic manifest
publication. Failed publication preserves the previous manifest. Old unreferenced
payloads are pruned; current Predictive and Live payloads are each <=512 KiB.

Automatic refresh defaults off. An optional timer checks at boot+45 seconds and
hourly thereafter, with up to 30 seconds jitter. It consumes the existing fresh
dashboard network decision, never establishes cellular/WireGuard or changes
uplink preference. Offline is a normal successful service outcome. Metered or
unknown metering blocks downloads by default; Wi-Fi is not assumed unmetered.
When a fresh uplink-manager record identifies the active device, the downloader
can read its NetworkManager metering property. Only explicit `no` admits an
unmetered download; missing/guessed metering remains conservative. No connection
activation or network setting write is performed.
Explicitly permit metered/unknown downloads if that matches the field data plan.

All attempts, including failed downloads, reserve quota before network I/O under
a downloader-only lock. Manual refresh cannot bypass quotas, retry backoff,
12-hour Predictive duplicate suppression or two-hour Live suppression. The
default payload reservation limit is 1 MiB/day. This bounds payload reads, not
TLS/DNS/IP overhead. Backoff is exponential and capped at one day. UTC moving
backwards defers requests. A missing/invalid credential never starts a request.

Live is disabled by default. Enabling it requires existing `ulorb_l1` entitlement
and explicit potential-charge confirmation; each web request also requires cost
confirmation. No timer requests Live data. Supported Live data is GPS/Galileo
ephemeris/almanac, supported health/time/ionosphere data and initial UTC aiding.
Only documented F10 subtype/length/version combinations pass the allowlist.
Live freshness is checked from initial time aiding and retained for at most two
hours. Absolute MGA-INI-TIME is removed from the injection selection: its
download-time value must never be replayed later from a host cache. Supported
GNSS UTC/time-offset models may still accompany ephemerides. The remote absolute
time value is never injected or used as a trusted system UTC source.

## Startup and fallback limits

Ordinary acquisition always starts independently of Internet. By default only
the host cache is refreshed. Optional boot injection reads a valid, identity-bound
Live cache when enabled, else only **today's** MGA-ANO subset from Predictive
cache. It does not inject a multi-day archive. With no usable host data, the
receiver continues with its retained Autonomous data or ordinary acquisition;
PCS does not calculate or select satellite orbits itself.

The conservative boot path requires receiver-resolved UTC, matching host/receiver
calendar date, and no existing usable fix. A lost backup clock or uncertain date
therefore falls back to ordinary acquisition. No host/remote clock value is
invented to accelerate a cold start. Fresh Live retrieval does not block UART
admission: download completion after admission only prepares a later startup.
There is no automatic paid request or hot Live injection during acquisition.

Every injected frame must receive a matching MGA-ACK. Injection has a three-second
frame budget; individual admission exchanges use a 250 ms timeout, plus bounded
readback/cleanup. Incomplete/rejected injection leaves accepted volatile aiding
in place and resumes ordinary acquisition; it never resets a synchronized
receiver to attempt undo. Failed restoration leaves a recovery journal.

## Autonomous and unavailable operations

Enable/disable on the web saves a **pending maintenance request**. It does not
claim the receiver was changed. In a separately authorized exclusive maintenance
session, `pcs-gnss-info maintenance-apply` with the same confirmation phrase
reads current configuration, applies only the requested ANA RAM bit, checks ACK
and readback, and records the outcome. Default orbit-error limits are unchanged.
ANA RAM settings can be lost on complete power loss; current receiver reports,
not saved operator intent, determine the displayed enabled state.

Manufacturer documentation notes that disabling Autonomous can delete retained
Autonomous observation data if suitable receiver flash is present. This candidate
does not qualify that storage behavior. Requesting disable is therefore an
explicit maintenance choice, not a routine diagnostic operation.

DBD backup/restore is documented by the firmware but not qualified on this
commissioned installation. Both buttons remain disabled. ITFM enablement is
documented and read back by maintenance inspection; the web never changes it.
Spectrum rate changes, DBD preservation/restoration, position optimization,
time aiding after complete backup-memory loss and hot assistance injection remain
unavailable during normal timing operation. These limits are deliberate;
hardware-dependent qualification is required before widening the interface.

For staging, rollback, validation and resource budgets see the
[installation guide](gnss-intelligence-install.md) and
[candidate acceptance report](v2.4.1-validation.md).
