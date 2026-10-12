# Inactive recovery preparation (candidate, not appliance acceptance)

The new tools implement **inspect → plan → stage → validate**. They do not
implement activation, install packages, contact NetworkManager, configure the
switch, or change a running service. Use synthetic data or an explicitly trusted
backup on an isolated recovery workstation. Do not point tests at production state.

The existing `pcs_reinstall_restore.py` identity/network restore path is unchanged.
The base installer does not call this staging tool or automatically restore VLANs.
New exports add a verified manifest; old exports remain readable. A complete
current-topology reinstall and physical recovery are still separate acceptance gates.

## Manifest contract, schema 1

`pcs-reinstall-state.sh --export` now passes its captured tar through
`pcs_recovery_manifest.py seal-stream` before encryption. This creates
`etc/pcs/recovery-manifest.json` **inside the encrypted backup**, never in live
`/etc/pcs`. The filter validates archive boundaries, retains original tar
file/directory metadata and verifies its result before emitting it. Failure stops
the existing pipefail export path and removes the incomplete encrypted output.
The normal export still requires the normal Pi user, expected source path,
noninteractive sudo and separate mounted backup media.

The versioned manifest contains:

- UTC creation time, source checkout commit/tag description and dirty flag;
  a SHA-256 fingerprint of machine ID, never its raw value. This identifies the
  export source checkout, not proof that every installed binary matches it.
- Every regular payload file's SHA-256, size, numeric source owner/group and
  permission mode. The manifest excludes itself from its file list.
- NetworkManager profile UUID/type/interface, VLAN parent/ID and IP methods;
  invalid profile paths are marked without including parser errors or secrets.
- Captured topology marker or explicit `unknown`, present policy components,
  minimum component package dependencies, and boundaries requiring manual work.

Metadata contains no raw keys, passwords, APNs, SSIDs or calibration claims.
Filenames, UUIDs, hashes and the machine fingerprint are still private operational
metadata. Keep manifests and plans private. Configuration values remain only in
the protected payload. Directory ACL/xattrs stay in the tar for preservation;
inactive staging intentionally applies its own private permissions instead.

Per-file hashes are recomputed when inspecting a manifest-bearing archive. They
detect omission, addition, changed content or recorded metadata mismatch. The
encrypted archive's existing `.sha256` sidecar independently checks transport;
the decrypted tar can also be pinned with `--sha256`. **Hashes are not signatures**:
an attacker who replaces both archive and checksum can replace the manifest too.
The existing AES-CBC format is retained for compatibility and is not authenticated
encryption. Keep a separately trusted digest/copy and passphrase. An authenticated
envelope would need a versioned migration, not a silent format change.

Old archives without a manifest report `absent-legacy`, with source version/time
unknown. No metadata is invented. The request below can supply an explicit topology,
but does not retroactively certify source identity. A missing mode marker is staged
only from that explicit request, with `origin: explicit-request` in the plan.
An existing incompatible marker is rejected. Invalid manifest-bearing archives
are never silently treated as legacy. Archive links/devices, traversal, ambiguous
cross-platform filenames, duplicate paths, file/directory collisions, excessive
entries and oversized payloads are rejected. Historical unsafe archives require
manual examination; no unsafe compatibility bypass is provided.

## Offline procedure

Keep decrypted material on private storage. After checking the encrypted archive's
trusted checksum, decrypt with the existing OpenSSL format into a mode-0700 review
directory, using `umask 077` and OpenSSL's interactive passphrase prompt. Do not put
the passphrase in an argument, environment file, report or shell history. The new
tools accept the resulting `.tar.gz`; they never decrypt or request credentials.

Run from a reviewed source checkout. Python 3 is required. Bandwidth validation
requires PyYAML; deeper keyfile validation needs Debian packages `python3-gi` and
`gir1.2-nm-1.0` (libnm 1.52 or newer for PCS shared-DHCP keyfiles). These are tool
dependencies, distinct from the packages/services listed for later installation.

```bash
python3 scripts/pcs_recovery_manifest.py inspect /private/review/state.tar.gz
```

Create a private JSON request using actual captured UUIDs from inspection. Schema
1 supports the implemented native VLAN layout and legacy `eth0` LAN with an
explicit, distinct Ethernet WAN. It does not discover adapters or remap identities.
VLAN example (replace all three UUID placeholders):

```json
{
  "schema": 1,
  "mode": "vlan",
  "lan_uuid": "CAPTURED-LAN-UUID",
  "wan_uuid": "CAPTURED-WAN-UUID",
  "parent_uuid": "CAPTURED-PARENT-UUID",
  "interfaces": {"lan": "eth0.10", "wan": "eth0.20", "parent": "eth0"}
}
```

For legacy use `mode: legacy`, the captured Ethernet LAN/WAN UUIDs, `parent_uuid:
null` and `interfaces: {"lan":"eth0","wan":"EXPLICIT-WAN","parent":null}`.
MAC-only WAN policies without an offline-resolvable interface/profile, extra
Ethernet WANs, unsupported VLAN layouts, missing profiles or malformed metadata
fail closed. No replacement hardware identity is guessed.

```bash
python3 scripts/pcs_recovery_stage.py plan /private/review/state.tar.gz \
  --request /private/review/request.json --output /private/review/plan.json
python3 scripts/pcs_recovery_stage.py init /private/review/workspace
python3 scripts/pcs_recovery_stage.py stage /private/review/state.tar.gz \
  /private/review/plan.json /private/review/workspace --dry-run
python3 scripts/pcs_recovery_stage.py stage /private/review/state.tar.gz \
  /private/review/plan.json /private/review/workspace
python3 scripts/pcs_recovery_stage.py validate /private/review/state.tar.gz \
  /private/review/plan.json /private/review/workspace --libnm
```

`plan` is non-mutating except for its new explicit output file. Optional
`--target-root /private/offline-root` inspects an offline target for differing
files and duplicate/core-interface profile conflicts; `/` is forbidden. Without
it, target conflicts are **not assessed**. A conflicting plan is still written
for review, exits nonzero, and cannot be staged. `--sha256 HASH` pins the input.
Stage and validate rebuild the plan and recheck target state to detect drift.

The plan lists every staged file/hash, original/request origin, intended ownership
and permission, profiles, component packages/services, excluded files and conflicts.
Dependencies are installation requirements, not a claim they are installed in the
target. No generated firewall, service unit, runtime counter or activation state is
restored. For VLANs the plan explicitly requires the generated guard rules, guard
service and NetworkManager ordering drop-in. Those need reviewed physical management
identities and must not be silently replayed from an old backup. Cellular profiles
also identify ModemManager as a dependency; no modem/session is started. Saved
installer settings are parsed without shell execution and staged
for review; never source them automatically from the staging directory.

Stage writes only `<workspace>/staged/rootfs` and its receipt. The workspace must
be newly initialized; an existing live directory cannot be adopted. Directories
are 0700 and files 0600, owned by the invoking user. On Windows the tool can exercise
logic, but POSIX permission/ownership acceptance must be performed on Linux.
The plan records eventual install permissions; source UID/GID values are not
blindly applied on a different machine. Symlinks and staged hardlinks are rejected.

Original profile/policy bytes are retained, including autoconnect and enabled
flags. They are inactive solely because they are outside live configuration paths.
**Do not copy the rootfs wholesale into `/` or load its profiles into NetworkManager.**
The mode marker, service enablement and network activation need coordinated review.
SNMP mapping receipts do not requalify a replacement switch; external switch setup
remains manual. Power calibration bytes are preserved, not measured or certified.
LCD layout/pins live in reviewed software; installer selections are captured in
the answer file, and GPIO shutdown marker files are regenerated by installation.

## Repeatability and rollback

Repeating stage with the same verified plan returns `unchanged`, after rechecking
all staged bytes and permissions. Different or altered state is rejected rather
than overwritten. A new generation is built in a fresh `.pending-*` directory and
renamed only after validation. Ordinary failure removes only that invocation's
pending directory. An exclusive workspace lock prevents concurrent writers.

```bash
python3 scripts/pcs_recovery_stage.py rollback /private/review/workspace
```

Rollback retires the inactive generation under a unique `retired-*` name and
preserves its evidence. It does not undo live configuration because none was
applied. Repeat stage then creates a fresh inactive generation. Unrelated workspace
files and offline target files are preserved. A killed process may leave a private
`.pending-*` directory or `.lock`; retain it for diagnosis and use a newly initialized
workspace. The tool does not automatically delete old evidence or stale locks.
Atomic staging is not a power-loss-tested filesystem transaction.

## Separate activation gate

No activation API or automatic installer hook is included. Before designing one:

1. Verify the intended replacement hardware, exact source, independent console/
   recovery path and a separately retained backup; resolve every plan conflict.
2. Install/regenerate the matching services, packages and firewall support while
   keeping recovered networking/services inactive. Existing component installers
   can start services, so they are not yet a safe unattended activation orchestrator.
3. Review full profile IP/routes/MTU, interface/parent/VLAN/UUID identities,
   management grants, radio/cellular permissions, and source-specific settings.
4. Design an activation transaction and independent rollback/watchdog covering the
   mode marker, profiles, uplinks, firewall and service start ordering together.
5. Prove interrupted activation/rollback on disposable systems, then schedule a
   separately authorized physical recovery and acceptance session.

Run `python3 tests/integration_recovery.py --run` only in a disposable Linux
environment with libnm, PyYAML and OpenSSL. It uses synthetic legacy/VLAN/Wi-Fi/
cellular data, exercises encrypted transport and repeated CLI staging, and rejects
corrupt/incomplete/conflicting requests. It does not install PCS or qualify hardware.
