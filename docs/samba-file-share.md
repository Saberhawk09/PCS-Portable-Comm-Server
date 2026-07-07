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

## Manual Backup Sync

Run:

```bash
./scripts/sync-pcs-share-to-backup.sh
```

Sync direction:

```text
/mnt/pcs-usb/PCS-Share -> /srv/pcs-share-backup
```

Warning: this is mirror-style sync.

If files are deleted from the USB primary share, the matching files may also be removed from the backup mirror during sync.

## Windows Client Access

From Windows File Explorer:

```text
\\10.42.0.1\PCS-Share
```

Backup share:

```text
\\10.42.0.1\PCS-Backup
```

If Windows prompts for credentials, use the PCS Samba credentials configured during setup.

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
- periodically run backup sync from PCS Control Panel or SSH
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
