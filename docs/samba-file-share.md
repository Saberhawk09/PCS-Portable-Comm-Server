# Samba File Share

PCS provides LAN file sharing for field clients using Samba.

The main use case is shared logging files for amateur radio events, especially situations where multiple Windows laptops need access to the same working folder.

## Current Share Layout

PCS currently provides two Samba shares:

```text
\\10.42.0.1\PCS-Share
\\10.42.0.1\PCS-Backup
```

Filesystem mapping:

```text
PCS-Share   -> /mnt/pcs-usb/PCS-Share
PCS-Backup  -> /srv/pcs-share-backup
```

On Windows and compatible network browsers, PCS advertises itself as
**PCS-FILE-SHARE**. The managed SMB host alias is also `PCS-FILE-SHARE`, while
`pcs-pi.local` and direct IP paths remain supported.

## Share Roles

### `PCS-Share`

Primary working share.

This is the main field share used by logging PCs.

Expected path:

```text
/mnt/pcs-usb/PCS-Share
```

Windows path:

```text
\\10.42.0.1\PCS-Share
```

### `PCS-Backup`

SD-card backup mirror.

This is a local backup copy stored on the Pi.

Expected path:

```text
/srv/pcs-share-backup
```

Windows path:

```text
\\10.42.0.1\PCS-Backup
```

## Storage Philosophy

The USB share is the working copy.

The SD-card backup share is the safety copy.

This protects against losing field logs if the USB drive is damaged, removed, or accidentally misplaced.

## USB Primary Share Setup

Run from the repository root:

```bash
./scripts/setup-usb-primary-share.sh
```

Default expected USB UUID:

```text
340B-4403
```

Run with a specific device path:

```bash
./scripts/setup-usb-primary-share.sh /dev/sda1
```

Supported filesystems:

```text
vfat
exfat
ext4
```

## Backup Share Setup

Run:

```bash
./scripts/setup-samba-backup-share.sh
```

This prepares:

```text
/srv/pcs-share-backup
```

and exposes it as:

```text
\\10.42.0.1\PCS-Backup
```

`PCS-Backup` uses the dedicated Samba username `pcs-admin` and the current PCS
web administrator password. The password helper updates both credentials as one
operation when the password changes through the web panel, Stats API/Android
app, or the interactive control-panel installer. `PCS-Share` retains its
existing Samba username and password.

On an upgraded PCS, the installer asks for the current admin password once to
initialize the dedicated backup account. It is sent only over local standard
input and is never stored as plaintext or placed in a process argument.

## Manual Backup Sync

Run:

```bash
./scripts/sync-pcs-share-to-backup.sh
```

Sync direction:

```text
/mnt/pcs-usb/PCS-Share -> /srv/pcs-share-backup
```

Backup sync is additive. If files are deleted from the USB primary share, the
matching files remain on the SD backup.

## Automatic Backup Sync

The base installer installs `pcs-backup.timer` and enables automatic backups
with a 10-minute default interval. The timer wakes every minute, but
`pcs-auto-backup` calls the existing fixed sync action only after the configured
interval is due. Manual and automatic dispatcher syncs share a non-blocking
lock, so they cannot copy the tree concurrently.

Open **Backup Settings** in the authenticated web menu, or **Configure** in a
paired PCS Companion app, to change enablement, the 1-43,200 minute interval,
or retained snapshot history. The root-owned policy is stored at
`/etc/pcs-backup/config.json`; do not edit it by hand while the timer is active.

```bash
systemctl status pcs-backup.timer --no-pager
sudo /usr/local/sbin/pcs-backup-config show
```

Automatic and manual syncs never delete destination files. When **Keep every
prior backup snapshot** is enabled, dated snapshots are stored under
`PCS-Backup-History`; unchanged files are space-efficient hard links, every
snapshot remains independently browsable, and no snapshots are pruned
automatically. Monitor SD-card free space when using history.

## Windows Client Access

With Windows Network Discovery enabled on a **Private** network, open
**Network** in File Explorer and select **PCS-FILE-SHARE**. PCS advertises only
on `eth0` and `wlan0`; it does not multicast discovery over cellular or
WireGuard. Direct paths continue to work when discovery is unavailable.

From Windows File Explorer:

```text
\\10.42.0.1\PCS-Share
```

Backup share:

```text
\\10.42.0.1\PCS-Backup
```

For `PCS-Share`, use the existing PCS Samba credentials configured during
setup. For `PCS-Backup`, use username `pcs-admin` and the current PCS web-admin
password.

Windows normally permits only one credential set per server name at a time. If
`PCS-Share` is already connected under its existing account, use the IP path for
that share and `\\PCS-FILE-SHARE\PCS-Backup` for the backup, or disconnect the
old session before authenticating as `pcs-admin`.

## Repeatable Discovery Setup

The base installer installs `wsdd2`, keeps its unrestricted vendor service
masked, and installs `pcs-wsdd.service`. The PCS service runs one WSD responder
so UDP discovery packets cannot be lost between competing processes. Its
dedicated nftables guard exposes WSD (TCP/UDP 3702) and LLMNR name resolution
(TCP/UDP 5355) only through `eth0` and `wlan0`, and drops both protocols on
WireGuard, cellular, and other interfaces. Avahi also
advertises `_smb._tcp` as **PCS File Share** for compatible non-Windows clients.

To reapply just this configuration:

```bash
./scripts/setup-pcs-share-discovery.sh
```

The script validates Samba before restarting it, verifies all three services,
and records a rollback snapshot under `/var/backups/pcs-share-discovery-*`.

## Windows Client Testing

Open Command Prompt:

```cmd
ping 10.42.0.1
```

Then test share discovery:

```cmd
net view \\10.42.0.1
```

Open the primary share:

```cmd
explorer \\10.42.0.1\PCS-Share
```

Open the backup share:

```cmd
explorer \\10.42.0.1\PCS-Backup
```

## N3FJP / Logging Software Use

For shared logging, point the logging software database or log file location to the primary share:

```text
\\10.42.0.1\PCS-Share
```

Recommended practice:

- create a folder per event
- keep the active log file in that event folder
- confirm automatic backup health or run a manual final sync before teardown
- confirm backup share contains updated files before teardown

Example:

```text
\\10.42.0.1\PCS-Share\Field-Day-2026
```

## Permissions

The share should be writable by field clients.

Expected behavior:

- clients can create files
- clients can edit files
- clients can delete files
- backup mirror can be updated by the sync script

If clients cannot write to the share, check Samba permissions and filesystem mount options.

## Service Checks

Check Samba services:

```bash
systemctl status smbd
systemctl status nmbd
```

Restart Samba:

```bash
sudo systemctl restart smbd nmbd
```

Check configured shares:

```bash
testparm -s
```

## Storage Checks

Check mounted storage:

```bash
lsblk -f
findmnt /mnt/pcs-usb
df -h
```

Check share directories:

```bash
ls -lah /mnt/pcs-usb/PCS-Share
ls -lah /srv/pcs-share-backup
```

## Common Problems

### Client cannot open `\\10.42.0.1\PCS-Share`

Check:

```bash
ping 10.42.0.1
systemctl status smbd
testparm -s
```

Also confirm the client has an address on the PCS LAN:

```text
10.42.0.x
```

### Share exists but USB storage is missing

Check:

```bash
lsblk -f
findmnt /mnt/pcs-usb
```

If the USB device is not mounted, reconnect it or rerun:

```bash
./scripts/setup-usb-primary-share.sh
```

### Backup sync fails

Check both paths:

```bash
ls -lah /mnt/pcs-usb/PCS-Share
ls -lah /srv/pcs-share-backup
```

Then rerun:

```bash
./scripts/sync-pcs-share-to-backup.sh
```

### Windows caches old credentials

On Windows:

```cmd
net use
net use \\10.42.0.1\PCS-Share /delete
net use \\10.42.0.1\PCS-Backup /delete
```

Then reconnect through File Explorer.

## Recommended Field Workflow

Before event:

1. Boot PCS.
2. Confirm clients can join Wi-Fi.
3. Open `\\10.42.0.1\PCS-Share`.
4. Create event folder.
5. Confirm all logging PCs can access the same folder.
6. Run a test log entry.
7. Run backup sync.
8. Confirm `\\10.42.0.1\PCS-Backup` contains the test data.

During event:

1. Use `PCS-Share` as the active logging location.
2. Run backup sync periodically.
3. Avoid removing USB storage while PCS is powered.

After event:

1. Run final backup sync.
2. Confirm backup share contents.
3. Shut down PCS cleanly.
4. Remove USB storage only after shutdown or confirmed unmount.
