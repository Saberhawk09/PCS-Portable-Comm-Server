# Field qualification

This is an explicit, local, supervised qualification tool. It is not part of the
base installer, web panel, power stress runner, uplink controller, or RF services.
The fixed scenarios are `FQ-001` (cached observation), `FQ-002` (independent
expiry of a harmless marker), and `FQ-301-v4` (guarded IPv4 Ethernet WAN fault).
The WAN work is local development; it has not been deployed to the commissioned
PCS. Observation PASS alone does not qualify or authorize a WAN fault.

## FQ-301-v4: current scope and gates

`pcs_qualify_wan.py` compiles transactions and validates identities/routes;
`pcs_qualify_fault.py` owns the table lifecycle;
`pcs_qualify_scenario.py` runs the fixed scenario;
`pcs_qualify_lan.py` is the Linux/Windows witness and PCS receipt protocol;
`pcs_qualify_windows.py` supplies the native Windows client checks.
PCS-side modules are included by the explicit qualification installer. The Windows
client helper is copied alongside the witness on Windows. Normal PCS installation
and production services are unchanged.

This increment fixes the first scenario's intended simulated condition: IPv4
egress blackhole on the verified Ethernet WAN, retaining carrier, addressing,
on-link traffic, DHCP, IPv6, and Starlink telemetry. Both PCS output and client
forwarding are affected. It does not simulate unplugging Ethernet, loss of power,
IPv6 failure, or necessarily loss of existing inbound-only traffic. With working
IPv6, applications may continue using IPv6; that is expected for this scenario.
Ethernet carrying a home uplink must be reported as such, not as live Starlink.

The compiler uses exclusive creation of `inet pcs_qualification`, priority -5,
a session-specific table comment, and an interface-index set with a 5–120-second
kernel timeout. It never modifies another table or an interface. Local exceptions
use return in this qualification chain and do not bypass other firewall chains.
Cleanup validates the session comment and uses the kernel table handle; a replaced
table with the same name is not deleted. Kernel expiry stops the fault but leaves
the inert table for independently scheduled cleanup. Neither primitive renews a
fault. The runner never renews a fault. The CLI accepts 60–120 seconds, default 90;
the smaller compiler bounds exist for disposable integration tests.

The first control gate is deliberately narrow: direct IPv4 SSH over eth0 on the
verified LAN subnet and only standard IPv4 routing-policy rules. The route query
includes the actual SSH server source address. This catches a Wi-Fi-address SSH
connection whose replies leave through Ethernet. Unknown, indirect, WireGuard,
IPv6, and policy-routed control paths are blocked by this new gate; the existing
Phase 1 advisory preflight is unchanged. No claim is made that a working WireGuard
tunnel is broken. Supporting it for injection requires further underlying-route
and endpoint-change handling.

Local validation commands:

```sh
python3 -m unittest discover -s tests -p 'test_pcs_qualify_wan.py' -v
# ONLY in the explicitly marked disposable QEMU guest:
python3 tests/integration_qualification_wan.py -v
```

The real namespace suite covers output and forwarded IPv4 drops, recovery, on-link
and LAN reachability, preserved IPv6/telemetry, kernel expiry without a userspace
runner, independently armed systemd cleanup after SIGKILL, table collision and
replacement protection, and coexistence with the actual PCS WireGuard/stats API
firewall scripts. A fixture with an existing drop confirms local exceptions do not
weaken that rule. These are isolated topology tests, not appliance acceptance.

The runner cross-checks the configured Ethernet/Wi-Fi identities against fresh
uplink caches, verifies the permanent Ethernet MAC/ifindex, the single IPv4 address
on each LAN/WAN interface, policy rules, and the source-specific SSH return route.
It repeats the checks immediately before the nft transaction and every checkpoint.
Changed identities/configuration abort; no interface, connection, or route is adopted
or repaired. It requires healthy preferred Ethernet and healthy Wi-Fi standby.
Poll/failure/recovery/probe bounds are 10/30/30/2 seconds respectively; configurations
outside those upper bounds are BLOCKED, not silently changed. Configured failure
and recovery hysteresis shorter than one second is outside this scenario.

There is a 45-second witness-readiness window, then the bounded fault, then up to
90 seconds for preferred-WAN recovery. Events record sampled detection, fallback,
fault removal, and recovery. Timing is observation latency, not an exact packet
outage measurement. Hysteresis checks reject transitions shorter than the configured
threshold minus one policy poll and one five-second checkpoint interval. This is
a conservative sampling bound, not subsecond policy timing certification.

PASS requires observed target IPv4 failure, Wi-Fi fallback, preferred Ethernet
recovery, required health, and LAN witness coverage through recovery. Transient
IPv4 loss is expected; power warnings yield PASS WITH OBSERVATION. A previously
healthy target IPv6 path regressing is FAIL. Changed cellular ownership/suppression/
active/link/address state is FAIL; the harness never connects or disconnects it.
No field-LAN witness means BLOCKED. Missing/stale/protocol-invalid witness evidence
is INCONCLUSIVE; an observed HTTP failure is FAIL. Lost lease or changed identity
is ABORTED. Cleanup/transaction errors are HARNESS ERROR. The seven result meanings
remain distinct.

**RF gate:** installed uplink recovery is allowed when both RF engines are
quiescent. Dire Wolf must be loaded (or masked), inactive/dead, with no main or
control PID and no queued job. Graywolf must meet the same conditions or be
unambiguously absent. Unknown, failed, transitional, or active engines BLOCK.
The recovery service must also be idle, with no queued job: an in-flight recovery
could already have passed its final active-engine check before an operator stop.
The gate never cancels that work; wait for it to finish and recheck engine state.

The existing `pcs-aprs-ptt-safe.service` must be active/running. Its read-only
`pcs-aprs-ptt-safe --check` must report GPIO6 output ownership by `pcs-ptt-safe`;
`pinctrl get 6` must report low. Service states are reread afterward and must be
unchanged. This first gate supports only the commissioned active-high GPIO6 on
gpiochip0; other arrangements, missing tools, malformed output, and collector
timeouts BLOCK. It observes software state, not physical RF silence. The helper's
check alone proves ownership, not voltage, which is why the pin-level observation
is required. The harness never invokes the helper's `--hold` or watchdog actions.

`pcs-qualify preflight` includes `fq301_rf_safety`; its Phase 1 exit status remains
advisory and does not certify WAN admission. Inspect the nested gate result.
FQ-301 records sanitized `rf_safety` events at admission, checkpoints, and after
cleanup; the Markdown report shows the latest observation. Initial refusal is
BLOCKED, not a PCS failure. A changed/unknown RF state during a campaign aborts it;
a failed final RF check cannot produce PASS. Recovery becoming busy conservatively
aborts even if that invocation would have left the inactive engine alone.

Production APRS recovery, configuration, and engine control are unchanged. The
harness never stops, starts, restarts, masks, disables, or restores an RF engine.
Operators must keep engines inactive throughout this RF-silent test. Active-engine
APRS-IS recovery qualification remains a separate future supervised scenario.

### Supervised RF preflight validation (no fault injection)

After reviewing/installing this exact harness revision through its opt-in installer,
connect by the independent field LAN. On the commissioned PCS checkout:

```sh
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh
systemctl --failed
systemctl show -p LoadState -p ActiveState -p SubState -p Job \
  direwolf.service graywolf.service pcs-direwolf-uplink-recovery.service
sudo env SSH_CONNECTION="$SSH_CONNECTION" pcs-qualify preflight
# If Dire Wolf is active, expect fq301_rf_safety.gate=BLOCKED.
# Only the operator deliberately performs this normal stop:
sudo systemctl stop direwolf.service
# ExecStopPost requests the existing PTT guard asynchronously. Wait for active:
systemctl is-active pcs-aprs-ptt-safe.service
sudo /usr/local/sbin/pcs-aprs-ptt-safe --check
sudo /usr/bin/pinctrl get 6
sudo env SSH_CONNECTION="$SSH_CONNECTION" pcs-qualify preflight
```

Require RF gate PASS, both engines inactive, recovery idle, and GPIO6 low. If
Graywolf is active or anything is ambiguous, stop this procedure for operator
review. Do not automatically start the guard or change configuration to pass.
The initial normal self-test belongs before the deliberate engine stop; its
selected-engine-active expectation may fail while the operator intentionally
holds APRS inactive. Record that expected difference; never reconfigure it away.
Review all other WAN/control/witness prerequisites before authorizing any fault.
After a separately authorized run, repeat the service/guard/pin checks, inspect
the report, run qualification cleanup and check normal PCS status/self-test.
Dire Wolf must still be operator-selected inactive. Only the operator may restore
APRS with `sudo systemctl start direwolf.service`, then rerun normal health checks.

### Independent LAN witness for the WAN scenario

Interface-bound WAN configurations may omit the optional activation profile UUID,
as supported by the production uplink manager. Qualification pins the observed
profile privately for the campaign and aborts if it changes; an explicitly
configured profile must still match. Qualification never edits these profiles.

On BCM2711 (Pi 4), `pinctrl get 6` can report `op -- pd | lo`: the drive
control is write-only, while `lo` is the independently read pin level. The RF gate
accepts either `dl` or `--` in that drive field, still requiring output mode,
pull-down, measured low, the existing PTT-safe owner and inactive RF engines.
High, unknown measured level, explicit drive-high, wrong ownership or malformed
output still blocks. This is observation only, not a GPIO write or RF override.
See the [upstream BCM GPIO implementation](https://github.com/raspberrypi/utils/blob/master/pinctrl/gpiochip_bcm2835.c).

The witness uses the standard library on a separate Linux or native Windows
client on the field LAN. Linux requires root for `SO_BINDTODEVICE`. It rechecks a direct
source-specific route before every HTTP probe and binds both its HTTP and UDP
sockets to the chosen interface and address. It does not use DNS/proxies/redirects
or record response bodies. The older portable observation witness remains available.

PCS opens UDP 39841 only during the campaign, bound to its LAN address and eth0.
It accepts only the SSH client's source address, matching random session ID, fixed
schema, sequential samples and bounded packets. There are no remote commands.
Each fresh HTTP GET is bracketed by PCS-timestamped before/after handshakes. Two
successful samples are required before injection. Receipt latency and intersample
gaps are bounded to three seconds; duplicate packets do not extend coverage.
All coverage decisions use the PCS monotonic clock and boot, not the client clock.
The final acknowledgement ends the client once recovery is covered. Its durable
file also records its own UTC/monotonic/boot/process-clock identity.

On PCS, a bounded spawned process owns receipt validation, monotonic timestamping
and UDP acknowledgements independently of synchronous health/status collection.
It retains the same eth0/address/source/session checks and packet/sample limits.
Private atomic IPC snapshots carry append-only receipt history to the campaign,
which remains the sole durable event writer. Closing the receiver freezes its
history and persists final receipts; a missing, stalled or inconsistent worker
cannot produce a PASS. The final done acknowledgement must actually have been
sent before successful completion. The worker shares the campaign's systemd
resource limits and control-group cleanup, exits on IPC loss, and has its own
360-second lifetime bound. It cannot change leases, firewall rules or RF state.
The Windows client's two-second socket timeout is unchanged. A receipt reply is
not a durable campaign result: interrupted or incomplete evidence remains
ineligible for a completed witness/PASS claim.

Server receipt events in `events.jsonl` determine the scenario result. Retain the
original client file as independent evidence; an interrupted client's partial file
is not a completed witness. This qualifies sampled HTTP availability, not DNS/NTP/
Samba or uninterrupted connectivity between samples. Do not open firewall ports or
change routing automatically to admit the witness; an unreachable receiver blocks
injection. No manual transaction or preflight override is supported.

After review, on an eligible system with RF/control gates satisfied:

```sh
# PCS, connected by direct field-LAN SSH; prints its session ID immediately:
sudo env SSH_CONNECTION="$SSH_CONNECTION" pcs-qualify run FQ-301-v4 --duration 90
# Separate Linux LAN client (Windows instructions below); replace the interface/address and printed session:
sudo python3 pcs_qualify_lan.py --session SESSION_ID --target 10.42.0.1 \
  --source 10.42.0.20 --interface eth0 --duration 240 --output witness.jsonl
# PCS, after completion:
sudo pcs-qualify report SESSION_ID
sudo pcs-qualify cleanup
```

These are supervised future operating steps, not authorization to deploy or a claim
that commissioned-PCS WAN acceptance has passed. Keep the normal self-test and
post-run production health validation separate from the synthetic VM results.

### Native Windows witness

After the campaign completes, preserve the original client JSONL and transfer it
to PCS. `sudo pcs-qualify witness SESSION_ID /path/to/witness.jsonl` recognizes
FQ-301 sessions and matches every sample to that session's durable PCS receipts.
Only a complete file covering fault onset through recovery can pass import;
truncation, mismatches, invalid clocks or missing recovery cannot pass. The import
summary is separate from, and never changes, the original campaign verdict.
This works for both native Windows and Linux FQ-301 client files. Keep the original
file alongside the exported session; import stores a sanitized summary only.

Use 64-bit Python 3.10+ on Windows 10 (1803+) or Windows 11. WSL is not this
backend. Copy **both** `scripts/pcs_qualify_lan.py` and
`scripts/pcs_qualify_windows.py` into the same local directory. No pip packages,
PCS-side Windows service, network changes, or automatic firewall exceptions are
needed. Use an operator-owned directory whose Windows ACL protects the evidence;
POSIX mode 0600 is not a Windows ACL. Existing output files are never overwritten.

Connect the Windows PC directly to the PCS field LAN, preferably by Ethernet.
The selected adapter must be connected physical Ethernet or Wi-Fi, with a preferred
assigned IPv4 address. Virtual/VPN adapters, weak-host forwarding configurations,
APIPA, default/gateway routes, host-route overrides, and ambiguous/unavailable
state are rejected. Keep the same PC and source address for SSH and the witness.

In PowerShell, identify the **field-LAN** adapter (do not select the home-WAN NIC):

```powershell
Get-NetAdapter | Format-Table ifIndex, Name, Status, HardwareInterface
Get-NetIPAddress -AddressFamily IPv4 |
  Format-Table InterfaceIndex, IPAddress, PrefixLength, AddressState
# Substitute the actual PCS login and verified field-LAN addresses:
ssh -b 10.42.0.20 pi@10.42.0.1
```

In that SSH terminal, follow the normal PCS health/RF preflight procedure. Only
after separate approval, start the existing bounded FQ-301 command. It prints a
session ID and waits up to 45 seconds for verified witness samples. In a second
**local Windows** PowerShell terminal, from the directory holding both scripts:

```powershell
# Replace SESSION_ID, source/target, and 7 with this PC's actual field-LAN ifIndex.
py -3 pcs_qualify_lan.py --session SESSION_ID --target 10.42.0.1 `
  --source 10.42.0.20 --interface 7 --duration 240 --output witness.jsonl
```

Windows `--interface` is a positive numeric **ifIndex**, not an adapter name.
The client uses read-only IP Helper APIs to verify adapter identity/state, address
ownership, strong-host policy, and the source-specific on-link route. It checks
before and after every HTTP probe and aborts if identity changes. Socket egress
is pinned with `IP_UNICAST_IF`; UDP receipt ingress is restricted with `IP_IFLIST`.
Unsupported socket options or failed readback abort rather than falling back to
source binding alone. These APIs do not require the witness to reconfigure Windows.
If security software prevents the exchange, the witness cannot establish coverage;
do not disable protection or bypass admission to obtain a PASS.

The existing PCS protocol, source-address check, timing bounds, and result rules
are unchanged. Windows evidence records platform `windows`, a per-process clock
ID and monotonic/UTC times; boot ID is explicitly null. Durations/coverage still
use PCS receipt timestamps, never subtraction of unrelated host clocks. Files do
not contain IP/MAC addresses, adapter identifiers, or HTTP response bodies.

Native Windows tests use real local Winsock TCP/UDP sockets with a loopback fixture;
only that fixture's admission and HTTP port are substituted. They do not prove
commissioned field-LAN reachability. The separate policy tests reject unsafe routes,
interfaces, and source states. Windows CI runs these alongside shared receipt tests;
Linux CI continues to run the full suite. Actual field-LAN witness coverage remains
required before an appliance injection can proceed.

API references: [GetBestRoute2](https://learn.microsoft.com/en-us/windows/win32/api/netioapi/nf-netioapi-getbestroute2),
[interface state](https://learn.microsoft.com/en-us/windows/win32/api/netioapi/ns-netioapi-mib_if_row2),
[Winsock interface options](https://learn.microsoft.com/en-us/windows/win32/winsock/ipproto-ip-socket-options).

Baseline inspected for this implementation: main
`f88e2b4e36477803ee17272ea5c6ee3b42d3e6b9`.

## Architecture and ownership

| File | Responsibility |
| --- | --- |
| `scripts/pcs_qualify.py` | Fixed CLI registry, systemd campaign, outcome aggregation, report/witness commands |
| `scripts/pcs_qualify_state.py` | Private sessions, bounded JSONL events, atomic/fsynced manifests, Markdown reports, locks |
| `scripts/pcs_qualify_observe.py` | Strict qualification allowlist, cache freshness, bounded read-only service queries |
| `scripts/pcs_qualify_safety.py` | Control-route assessment, prepared/active leases, independent expiry, own-artifact cleanup |
| `scripts/pcs_qualify_witness.py` | Standalone source-address-bound client HTTP sampling, durable JSONL, strict offline validation |
| `scripts/pcs_qualify_wan.py` | Pure fixed IPv4 transaction and identity/control validation |
| `scripts/pcs_qualify_fault.py` | Prepared WAN ownership, atomic table creation, independent verified cleanup |
| `scripts/pcs_qualify_scenario.py` | Fixed WAN admission, observation, failover/recovery and outcome evaluation |
| `scripts/pcs_qualify_rf.py` | Read-only inactive-engine, idle recovery and existing PTT-safe verification |
| `scripts/pcs_qualify_lan.py` | Interface-bound Linux/Windows witness and monotonic PCS receipt protocol |
| `scripts/pcs_qualify_windows.py` | Windows client-only native route, physical adapter, address and socket binding checks |
| `scripts/setup-pcs-qualify.sh` | Explicit install/check/remove, checksum ownership, rollback, retention of reports |
| `systemd/pcs-qualify-cleanup.service` | Boot cleanup before multi-user; incomplete sessions become ABORTED |

Installed CLI: `/usr/local/sbin/pcs-qualify`. Modules: `/usr/local/lib/pcs`.
There is no qualification configuration file or arbitrary command/plugin registry.
The wrapper uses isolated Python mode and adds only the root-owned PCS module path.

Each campaign runs in the transient `pcs-qualify-campaign.service`, with CPU,
memory, task, wall-duration and log-rate limits. A separate transient
`pcs-qualify-expiry.timer` starts `pcs-qualify-expiry.service`. Neither expiry nor
restore needs the campaign lock. If a stopped runner holds the mutation lock,
restore verifies its systemd InvocationID and transient FragmentPath before killing
that exact qualification invocation. It never accepts a PID or command from a lease.
Changing/unrecognized ownership fails closed.

`/run/pcs-qualification` is root-owned mode 0700; lease files and locks are 0600.
The active lease has UTC/monotonic/boot timing in its session and a same-boot
monotonic deadline. A prepared record precedes timer registration. The private
marker is written only after systemd confirms the timer is active. A death before
registration leaves no effect; next startup/boot cleanup clears the prepared record.
No lease renewal exists. Maximum lease: 360 seconds.

The marker remains the entire FQ-001/FQ-002 effect. For WAN, `wan.json` records
ownership before table creation; the active lease moves through prepared/active
with effect `nft-v4`. Independent expiry is armed before the transaction. Cleanup
removes the verified session-owned table before unlinking its ledger and marker.
It cannot restore PCS settings, restart a PCS service, reconnect cellular, write
I2C or change routes. Corrupt ownership fails closed and the kernel timeout still
bounds packet drops. Boot cleanup inspects only the fixed qualification table,
recognizes its exact ownership comment, and refuses foreign same-name tables.
Explicit `cleanup` also stops an identified qualification campaign and recovers
its unfinished session. Completed sessions survive cleanup and uninstall.

At boot, `/run` has already lost old leases. Boot cleanup marks unfinished sessions
ABORTED with a separate recovery JSONL record. It does not replay an effect or
restore a prior boot's service/network state. The campaign invokes the same
recovery before starting, to handle killed runners without requiring a reboot.

## Evidence and result contract

FQ-001 samples immediately and approximately every five seconds, including a final
checkpoint. Duration is 5–300 seconds (default 60). It reads:

* `/run/pcs-power-monitor/status.json`: version, collection age, boot identity,
  overall status, and status/voltage/current/power for the four commissioned roles
  `input`, `rail_5v`, `rail_12v`, `starlink`. Maximum age is 10 seconds.
* `/run/pcs-uplink-manager/status.json`: version, collection age, boot identity,
  manual/auto mode, IPv4 internet flag, and numbered slots containing type, link,
  IPv4/IPv6 address-present and probe-health flags, active/selected flags,
  cellular ownership/suppression flags. Maximum age is 30 seconds.
* `systemctl show --property=ActiveState` for the power monitor and uplink manager.

The cache writers remain the owners. No direct INA226/I2C access is made and
`NetworkManager.observe()` is not called (it adjusts reverse-path filtering).
The uplink manager's IPv6 selected flag is not relabeled as observed effective
routing. Unknown probe health stays unknown. Collector error text becomes a boolean.

Evidence excludes IP/MAC addresses, SSIDs, profile names/UUIDs, keys, passwords,
GPS coordinates/grid squares, raw exceptions, process command lines, journal
text, HTTP bodies, and general dashboard/self-test dumps. Even an invalid string
in an allowed typed field is rejected rather than copied. No raw source cache is
saved. This allowlist is separate from both public dashboard and stats API filters.

All PCS events contain sequence, UTC, monotonic seconds and boot ID. Durations
use monotonic time. Cross-boot data, backward/future cache timestamps, and detected
UTC-versus-monotonic discontinuities cannot produce PASS. Cache freshness is sampled;
it is not proof of uninterrupted sensor collection between checkpoints.

| Outcome | Meaning |
| --- | --- |
| PASS | All required samples for this narrow scenario satisfy its checks |
| PASS WITH OBSERVATION | Complete evidence, but power warning or no IPv4 internet observed |
| FAIL | A collected required service/power failure, or failed marker expiry |
| INCONCLUSIVE | Missing/malformed/stale/unknown evidence or a detected clock discontinuity |
| ABORTED | Interrupted/expired campaign or boot recovery of an unfinished session |
| HARNESS ERROR | Execution, evidence-write or cleanup failure |
| BLOCKED | Prerequisite, identity/ownership, storage or lock prevents a safe run |

Exit codes respectively: 0, 0, 1, 2, 3, 4, 5. Preconditions that prevent creating
a session print BLOCKED without creating a misleading report. A manifest with
`complete: false` is **not** a completed result, regardless of its provisional
`result` field. Completion is committed only after evidence and report writes.
Known FAIL across checkpoints takes precedence over an incomplete other checkpoint;
the incomplete checkpoint remains visible in the events.

Sessions are root-only under `/var/lib/pcs-qualification/sessions/<32-hex-id>`:
`session.json`, `events.jsonl`, `report.md`, optionally `recovery.jsonl` and
`witness.json`. Keep the whole directory when exporting. Reports are sanitized,
but remain private operational evidence unless an operator elects to share them.

Storage is bounded to 32 sessions and a 64 MiB aggregate admission limit with
space reserved for the next run; require at least 128 MiB free before admission.
An event file is capped below 2 MiB. Commands have five-second timeouts and 64 KiB
output caps. Campaigns have 128 MiB memory, 20% CPU quota, 16 tasks, 360-second
systemd lifetime and 30 CPU seconds. No automatic pruning deletes evidence.
At a capacity block, export and verify completed sessions, then have the operator
remove only the explicitly selected completed session directories. Do not clear
the state tree or remove an active session.

## Control-path preflight and isolation

Preflight requires root, systemd, and enabled boot cleanup. It treats absent
`SSH_CONNECTION` as unknown: sudo/tmux/terminal state cannot establish a local
console. For SSH it resolves the route to the client. For `wg-pcs`/`wg-direct`, it
reads only current peer endpoints and resolves the actual outer endpoint route,
including IPv6 endpoints. Missing or multiple routes/endpoints remain unknown.
No private WireGuard configuration is read. Reports include only transport and
LAN/loopback/other/unknown categories, never the endpoint or interface address.

This is an advisory observation preflight. `network_mutation_allowed` is always
false, even for a verified route. No target interface argument or override exists.
The WAN scenario uses its own stricter admission path described above. This
advisory command is not an assertion that WAN admission has passed.

Existing PCS systems deliberately kept isolated:

* `pcs_power_stress.py`: stops the normal monitor and directly owns high-rate I2C;
  never run it concurrently with an observation intended to qualify normal power
  operation. Missing/stopped normal monitoring cannot pass this scenario.
* `pcs-web-action`: none of its mutation actions is reused. Modem restart includes
  USB replug; Wi-Fi/cellular disconnect alters operator policy; time sync steps
  clocks; storage actions can stop Samba/unmount/power off USB.
* GPSD/GNSS/Chrony/NMEA, Pi-Star, APRS/Dire Wolf/PTT and USB: no mutation and no
  physical/RF/time-source availability claim. The ordinary self-test is not invoked:
  it writes its own logs, can buzz, and WARN can still exit zero.
* All production nftables tables and NetworkManager shared-LAN rules: untouched.
  Namespace tests prove priority coexistence and kernel set expiry. Only the
  dedicated `inet pcs_qualification` table is owned by the WAN effect.
  The fixture invokes the current WireGuard and stats API firewall scripts inside
  the namespace, using temporary policy files; it does not install them on the guest.

## Standalone witness

Copy `scripts/pcs_qualify_witness.py` to a separate LAN client with Python 3.
It uses only the standard library. Supply numeric PCS and client LAN addresses;
the socket binds to that source address. Each sample opens a fresh HTTP connection
and requests `/` with a random query and no-cache headers. No proxy, DNS lookup,
redirect following, credentials or shared persistent connection is used. Only HTTP
200 with a body byte passes. The body and addresses are not logged.

Example (replace the session token and client address):

```sh
python3 pcs_qualify_witness.py --session SESSION_ID \
  --target 10.42.0.1 --source 10.42.0.20 --duration 90 \
  --interval 1 --output witness.jsonl
```

The file is created exclusively, flushed and fsynced per sample, including failures
while disconnected. It survives loss of PCS connectivity. Bounds are 5–600 seconds,
1–10-second intervals, two-second request timeout, 2 MiB file limit. A missing
trailer, sequence gap, excess scheduling gap, clock jump, wrong session or malformed
field is INCONCLUSIVE at import. Failures in a complete file are FAIL.
Client Linux boot ID is recorded when available; otherwise it is explicitly null,
with a unique per-process clock ID. Never compare two hosts' monotonic values.

After the PCS run ends, transfer the client file and import:

```sh
sudo pcs-qualify witness SESSION_ID /path/to/witness.jsonl
sudo pcs-qualify report SESSION_ID
```

The report includes a separate sanitized client result; retain the original client
JSONL alongside exported PCS evidence. Import does not upgrade the PCS result.
This minimal witness proves only its own HTTP samples, not DNS/NTP/SMB, absence
of short gaps between samples, NIC routing policy, or automatic cross-host time
alignment. A source-address bind alone is not an interface-route lock on a multihomed
client. Before a WAN continuity scenario, require an independently verified LAN-only
client path and explicit evidence that its sampling interval covers the fault and
recovery. Automatic cross-host correlation is **not yet an acceptance claim**.

## First supervised observation on the commissioned PCS

Initial framework development was offline. Commissioned-PCS observation and marker
expiry subsequently passed on v2.1.1, with a partial home-LAN witness trace. That
trace is not full field-LAN WAN-fault coverage. The following observation steps
remain the reproducible baseline procedure.

1. Review this Phase 1 change and local acceptance evidence. Arrange a reliable
   LAN/console control path. Do not run power stress or other commissioning changes
   concurrently. Transfer the reviewed tree locally; no remote deployment is implicit.
2. From that reviewed checkout on PCS:

   ```sh
   sudo bash scripts/setup-pcs-qualify.sh --install
   sudo bash scripts/setup-pcs-qualify.sh --check
   sudo pcs-qualify preflight
   sudo pcs-qualify run FQ-002
   ```

   Require marker-expiry PASS; inspect unknown control-path results rather than
   treating them as permission for later network faults. No reboot is needed here.
3. Run `sudo pcs-qualify run FQ-001 --duration 60`. It immediately prints its session
   ID. Optionally start the separate client witness with that ID. Because it starts
   later, do not claim witness coverage of the beginning of this first run.
4. Review `sudo pcs-qualify report SESSION_ID`, the JSONL checkpoints, and the
   manifest's `complete: true`. Import a completed witness if used. Missing evidence
   is a result to investigate, not a reason to lower freshness/schema requirements.
5. Verify `sudo pcs-qualify cleanup` succeeds and no `active.json` or `marker`
   remains in `/run/pcs-qualification`. Keep/export the session directory and original
   witness file. Review health and any PCS defects with the operator.
6. Optional removal from the same checkout:
   `sudo bash scripts/setup-pcs-qualify.sh --remove`. Evidence is preserved.

Use `sudo pcs-qualify cleanup` from a second control terminal to abort a campaign.
If the foreground connection disappears, the systemd campaign and expiry remain
bounded independently of that connection. Do not interpret disconnected terminal
output as a successful completed run; inspect the persisted manifest.

## WAN development acceptance (2026-09-29)

All work in this section ran locally in the marked disposable Debian 13 QEMU guest.
No new harness code or WAN fault was deployed to the commissioned PCS.

| Executed check | Result |
| --- | --- |
| Final full Linux unit discovery | 700 passed, no skips |
| Original privileged Phase 1 regression suite | 8 passed |
| WAN kernel/installed lifecycle suite | 10 passed |
| Complete campaign with independent LAN witness; induced HTTP outage | 2 passed |
| Actual guest reboot with an interrupted FQ-301-v4 lease | Passed; table/runtime state absent, session ABORTED |
| Post-boot installer check and failed-unit inspection | Passed; no failed units |
| Python compileall, installer shell syntax, staged whitespace | Passed |

The full campaign tests use real network namespaces, UDP receipts, interface-bound
HTTP requests, systemd, nftables, session files, and report generation. Physical NIC
identity, sensor readings, and NetworkManager policy observations are supplied by
explicit test fixtures. They do not certify real appliance failover, RF behavior,
paid-cellular behavior, or the commissioned topology. Unit tests separately exercise
the actual preflight collector's source-route, identity, and RF refusal checks.

The final lifecycle suite includes independent expiry after SIGKILL at the ownership
record and nft commit, SIGSTOP while holding the mutation lock, and SIGKILL with
the transaction batch still open. The batch now uses a fixed private `nft.batch`
path, which restore removes after verified cleanup rather than leaving crash debris.
Installer tests also prove new unowned module targets are preserved and rejected.

Reboot evidence: boot ID `95a34ad2-5807-4c8e-8ee8-a839fb2639f5` changed to
`83f796be-427d-4d21-910c-eb9b46329e54`; session
`b1be656c7a7d4f38844f1f2a02b9cc31` became ABORTED. The boot fixture deliberately
targets an unused ifindex, so it cannot disrupt the guest's management network.
The original 31 completed fixture sessions were archived and verified before
removal to make room under the unchanged 32-session limit.

Additional privileged reproduction commands, **disposable guest only**:

```sh
python3 tests/integration_qualification_wan_lifecycle.py -v
python3 tests/integration_qualification_wan_campaign.py
python3 tests/integration_qualification_wan_lifecycle.py --prepare-boot
# Reboot the disposable guest externally, then:
python3 tests/integration_qualification_wan_lifecycle.py --verify-boot
```

Logs and private session evidence are retained in the workstation's qualification
VM evidence directory. Review these local results before any appliance deployment.
Commissioned WAN acceptance remains blocked by the independently verified field-LAN
control/witness requirement and the downstream RF-recovery gate described above.

## Phase 1 acceptance history and reproducible tests

Local validation completed 2026-09-28 on a disposable Debian 13 amd64 QEMU guest,
Python 3.13, systemd `257.13-1~deb13u1`, nftables `1.1.3`:

| Executed check | Result |
| --- | --- |
| Full Linux unittest discovery (including 20 qualification tests) | 668 passed, no skips |
| Real privileged qualification suite | 8 passed |
| Actual guest reboot and post-boot install check | Passed; lease absent, interrupted session ABORTED |
| Frontend JavaScript tests | 17 passed, no skips |
| Python compileall; all shell syntax and shell executable modes | Passed |
| Git staged whitespace check | Passed |

The reboot evidence records boot ID `6866e36a-d411-451e-8607-4530f6b42a45`
changing to `8cd53cda-ad3c-4169-9ccf-0b74a6607568`; interrupted test session
`26adc25c145e45039e279ec42b5f22c6` was recovered by the installed boot service.
Test logs and the machine-readable acceptance record were retained on the local
development workstation. These are local framework results, not commissioned-PCS
or Raspberry Pi hardware acceptance.

No production PCS file was changed. The stopped-mutation-owner and delayed-expiry
reviews led to two harness safeguards: exact systemd invocation ownership before
terminating a blocked runner, and session-bound expiry so an old timer cannot
revoke a newer lease. Corrupt persistent manifests recover to HARNESS ERROR without
exporting their raw contents; other unfinished sessions still recover.

Normal Linux tests: `python3 -m unittest discover -s tests -v`.
Qualification-only tests: `python3 -m unittest discover -s tests -p 'test_pcs_qualify*.py' -v`.

Privileged integration is refused unless the environment is QEMU/KVM and has
`/etc/pcs-qualification-disposable` containing `local-qemu-acceptance-only`.
Create this marker **only in a disposable guest**. Required guest tools include
Python, systemd, nftables, iproute2 and ping. Never add that marker to PCS.

```sh
python3 tests/integration_qualification_safety.py -- --run
python3 tests/integration_qualification_safety.py -- --prepare-boot
# Reboot the disposable guest externally. This is not an installed scenario.
python3 tests/integration_qualification_safety.py -- --verify-boot
```

| Gate | Required evidence before advancing |
| --- | --- |
| Observation isolation | Real CLI cache fixtures: PASS/stale INCONCLUSIVE/known FAIL; services stay active; nft ruleset unchanged |
| Evidence integrity | Typed canaries excluded, stale/wrong boot/nonfinite/malformed/timeout/truncation rejected; incomplete report cannot be claimed complete |
| Lease/crash isolation | SIGKILL after prepared/marker/active writes; SIGSTOP of mutation owner; real independent expiry; repeated cleanup and unrelated sentinel preserved |
| Locking/limits | Second campaign blocked; restore independent of campaign lock; private file modes; session/disk/event bounds fail closed |
| Kernel/boot behavior | Real namespace nft drop then timed recovery; unrelated tables survive; actual guest boot ID changes and unfinished session becomes ABORTED |
| Control safety | Unknown/ambiguous paths fail closed; observation preflight remains advisory; WAN requires direct independent LAN control |
| Witness | Source bind, HTTP failure/redirect, durable output, incomplete/wrong-run/clock/gap validation; live LAN path and coverage still require commissioning |
| Installation | Repeated install/check, failed-upgrade rollback, repeated remove, session retention, permissions |
| PCS commissioning | Observation/marker commissioning completed; full field-LAN WAN witness and WAN campaign remain pending |
| WAN authorization | Review Phase 1 results; exact WAN/control/LAN identity gates and scoped nft ownership/expiry must be implemented and tested separately before FQ-301-v4 |

Namespace nft tests do not exercise NetworkManager's real appliance-generated
rules, upstream WAN behavior, paid-cellular semantics, RF consequences or a live
WireGuard control tunnel. Those boundaries remain explicit outstanding gates.

The full suite should run under an ordinary user, matching CI. Running it as root
changes existing tests' token-helper and nginx privilege behavior. Build Linux
archives with `git -c core.autocrlf=false archive` so the Windows Git setting does
not convert shell-sourced `.conf` fixtures to CRLF. These are test-environment
requirements; no PCS code changes are needed for them.

## Repository inconsistencies relevant to later qualification

These pre-existing issues are documented here without changing PCS behavior:

* `scripts/README.md` still describes older cellular-fallback behavior while current
  setup delegates to the uplink manager; qualify current ownership/suppression rules.
* Power documentation contains historical planned-fourth-monitor and input-total
  descriptions. Current power caches use commissioned four-role, source-aware accounting.
* Historical pending items in `docs/testing-uplinks.md` are not a current inventory
  of automated versus appliance acceptance.
* `test-time-source-failover.sh` uses EXIT restoration; SIGKILL is outside that promise.
  It is not suitable as an independently expiring qualification injector.
* Pi-Star shutdown's configurable host and dashboard's hard-coded `10.42.0.3` can
  disagree. No Pi-Star result is inferred from either in this Phase 1 scenario.
* Public dashboard sanitization still permits location data, and the stats API has
  a different allowlist. Neither is a qualification evidence-export policy.

No discovered repository inconsistency above has been confirmed as a new live PCS
failure. Local fixture failures and harness defects must be separated from PCS defects.
