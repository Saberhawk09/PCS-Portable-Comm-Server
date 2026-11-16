# GNSS Intelligence staging, installation and rollback

These are instructions for a future **authorized** installation. No appliance
deployment is authorized by creating or testing this development candidate.
Do not run the full PCS base installer for this additive update.

## Stage without touching appliance paths

From the candidate checkout, create a new review directory (it must not exist):

```sh
python3 scripts/stage-gnss-intelligence.py stage --directory /tmp/pcs-gnss-info-stage
```

The numbered files and `manifest.json` contain the exact installation bytes,
SHA-256 hashes, destinations and modes. No secrets enter the bundle. Review
`git diff`, tests, the manifest and the candidate acceptance report first.

The installer has an exact destination allowlist: five Python library files,
the updated existing `pcs-gnss` admission helper, the existing web backend copy,
five new units, a fixed root helper wrapper, two-command sudoers allowlist and
one backend ExecStart drop-in. It refuses symlink targets/parents and checks
the complete stage before the first write. Existing root GNSS enrollment,
Chrony/GPSD configuration, GPIO boot settings, modem and networking settings are
outside the manifest.

## Install inactive, only after authorization

```sh
sudo python3 scripts/stage-gnss-intelligence.py install \
  --directory /tmp/pcs-gnss-info-stage \
  --confirm 'I authorize PCS GNSS software installation'
```

Preflight validates sudoers. The installer saves all replaced/absent destination
states, modes and ownership in a unique root-only backup under
`/var/backups/pcs-gnss-info/`, then atomically publishes files. A partial failure
restores those files. It runs `daemon-reload` but never starts, stops, enables
or restarts services. New settings default to disabled, including automatic
refresh, Live and boot injection. Existing assistance settings are preserved.

The web drop-in takes effect only at the separately approved backend restart.
The optional boot hook takes effect only on the next existing admission event;
installing files does not restart GPSD or Chrony.

After reviewing the inactive installation, an authorized operator can enable
passive diagnostics and the optional timer, and restart only the web backend:

```sh
sudo systemctl enable --now pcs-gnss-info.service
sudo systemctl enable --now pcs-assistnow-refresh.timer
sudo systemctl restart pcs-control-panel.service
```

The timer does no network work until Automatic Refresh is explicitly enabled.
Do not restart GPSD/Chrony merely to populate receiver firmware metadata or
obtain RF messages. Unknown diagnostics are an acceptable initial condition.

Verify the existing timing status, paired F10P, GPSD/manual/LAN feed contracts,
public privacy projection, admin authentication/CSRF, unrelated service states
and PCS self-test. Capture resource measurements on the Pi. Stop the new passive
service if it causes unexpected resource use; it is independent of timing.

## Rollback

Disable only the added timer/service and stop any queued assistance jobs:

```sh
sudo systemctl disable --now pcs-assistnow-refresh.timer pcs-gnss-info.service
sudo systemctl stop pcs-assistnow-refresh.service pcs-assistnow-manual.service pcs-assistnow-live.service
sudo python3 scripts/stage-gnss-intelligence.py rollback \
  --directory /var/backups/pcs-gnss-info/EXACT_BACKUP_DIRECTORY \
  --confirm 'I authorize PCS GNSS software installation'
sudo systemctl restart pcs-control-panel.service
```

Use the exact printed backup directory, not a guessed newest backup. Rollback
refuses to remove the receiver-management code while a RAM recovery journal is
pending. Resolve that journal with the matching receiver in an authorized
maintenance session first; do not discard its identity guard.
Rollback
preflights every installed hash and refuses to overwrite later edits. A manual
review is required if another update changed those files. It restores old modes,
owners and the prior backend drop-in/absence, removes only its own new files,
and reloads systemd. GNSS/PPS services are not restarted. Do not disable units
that predated this candidate during a subsequent upgrade rollback; retain the
recorded pre-install service state in the deployment record.

Rollback leaves credentials, assistance settings, cache and RF history private
and intact for operator recovery. It does not undo an explicitly commissioned
receiver RAM feature. Any such reversal needs the same maintenance authorization
and readback; do not reset the receiver or persist settings to flash.

## Failure recovery

- No network/credentials/entitlement: normal cache/Autonomous/ordinary acquisition.
- Invalid/corrupt/expired payload: reject it; current valid manifest survives a
  failed replacement. Clear Expired Cache also removes unreferenced payloads.
- Interrupted request: durable request reservation prevents quota reuse. Retry
  remains subject to backoff and the daily/monthly/data caps.
- Diagnostic crash: systemd can restart only the passive process; capture ends
  and no receiver rate changes need restoration. Page values become stale.
- Interrupted injection/configuration: `receiver-transaction.json` records the
  original allowed RAM setting. Existing exclusive admission restores it before
  proceeding. If readback fails, use approved maintenance; do not delete the
  journal to bypass the recovery check.
- Missing receiver/USB renumbering: retain v2.4.0 enrolled-alias recovery. The new
  passive process waits for GPSD. It never activates the EM7565 GNSS function.

Keep root-only backup copies of `/etc/pcs/assistnow.json`,
`/etc/pcs/assistnow/` and `/var/lib/pcs-assistnow/` in an appropriately encrypted
operator backup. These new optional stores are not automatically added to an
older captured recovery kit. Keep secrets out of Git and redacted exports.

## Expected resource budget

The passive process has a 96 MiB cgroup memory ceiling and 10% of one CPU quota.
Each short-lived downloader has 64 MiB/10% limits and a 30-second service timeout.
These are enforced limits, not Pi measurements. It reads at most two loopback
GPSD streams, runs one short read-only loopback Chrony source query every five
seconds, and writes an ephemeral diagnostic status every two seconds; it
writes persistent events only on changes. Capture retains 60 frames, history
120 signal samples/256 RF events and ten snapshots. Typical Python resident
memory is expected to be tens of MiB; measure the commissioned ARM64 installation
before accepting overhead.

Host payload cache is <=1 MiB for current Predictive+Live data. Snapshot reports
are bounded by the diagnostic parser (at most eight RF paths, 255 signals and
255 satellites); reserve 16 MiB for the ten reports and event/state files.
Installation/backups are separate and retained until operator cleanup. A hard
snapshot payload ceiling is enforced by the implementation; malformed/oversize
reports are not saved. Default download payload reservations are <=1 MiB/day;
normal successful free refresh is one archive per 72 hours, with actual size
dependent on service constellation/window output. TLS/DNS overhead is additional.

See [behavior and service registration](gnss-intelligence.md) and
[acceptance evidence](v2.4.1-validation.md).
