# Field qualification — Phase 1

This is an explicit, local, supervised qualification tool. It is not part of the
base installer, web panel, power stress runner, uplink controller, or RF services.
The only installed scenarios are `FQ-001` (cached observation) and `FQ-002`
(independent expiry of a harmless private marker). **No WAN injection is implemented.**
An observation PASS is not permission to run FQ-301-v4.

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

The marker is the entire Phase 1 effect. Restore removes only the fixed
`marker` and `active.json` entries. It cannot restore PCS settings, restart a PCS
service, reconnect cellular, write I2C, change routes or delete an nftables table.
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
Phase 2 must add permanent identity/ifindex revalidation, policy-route awareness,
LAN exclusion, and exact control-underlay exclusion before enabling any injector.
The Phase 1 checks must not be misrepresented as those future gates having passed.

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
  Namespace-only tests prove priority coexistence and kernel set expiry; there is
  no installed nftables fault code. A future dedicated `inet pcs_qualification`
  table needs its own ownership and restore implementation and review.
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

PCS was offline during development. These steps have not been performed there.

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

## Acceptance gates and reproducible tests

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
| Control safety | Unknown/ambiguous paths fail closed; WireGuard inner/outer route unit test; all network mutation remains disabled |
| Witness | Source bind, HTTP failure/redirect, durable output, incomplete/wrong-run/clock/gap validation; live LAN path and coverage still require commissioning |
| Installation | Repeated install/check, failed-upgrade rollback, repeated remove, session retention, permissions |
| PCS commissioning | Reviewed, supervised commissioned-PCS observation and witness evidence; **pending while PCS offline** |
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
