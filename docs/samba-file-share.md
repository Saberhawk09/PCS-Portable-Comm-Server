# PCS Samba File Share

PCS provides local file sharing over Samba for clients connected to the PCS network.

## Client Access

From a client connected to the PCS network:

```text
Primary share:  \\10.42.0.1\PCS-Share
Backup share:   \\10.42.0.1\PCS-Backup
```

## Current Share Layout

PCS currently uses removable USB storage as the primary file share and the Pi SD card as a backup mirror.

```text
\\10.42.0.1\PCS-Share   → /mnt/pcs-usb/PCS-Share
\\10.42.0.1\PCS-Backup  → /srv/pcs-share-backup
```

## Primary USB Share

`PCS-Share` is the main field file share.

Current tested USB device:

```text
Label:       LEXAR
UUID:        340B-4403
Filesystem: vfat
Mount:       /mnt/pcs-usb
Share path:  /mnt/pcs-usb/PCS-Share
```

Setup script:

```bash
./scripts/setup-usb-primary-share.sh
```

The setup script can use the default USB UUID:

```bash
./scripts/setup-usb-primary-share.sh
```

Or a device path:

```bash
./scripts/setup-usb-primary-share.sh /dev/sda1
```

Supported filesystems:

```text
vfat
exfat
ext4
```

## SD Card Backup Share

`PCS-Backup` is the backup mirror stored on the Pi SD card.

```text
Share path: /srv/pcs-share-backup
```

Setup script:

```bash
./scripts/setup-samba-backup-share.sh
```

This share is intended to remain available if the USB stick is removed or fails.

## Manual Backup Sync

Sync USB primary storage to SD backup:

```bash
./scripts/sync-pcs-share-to-backup.sh
```

Sync direction:

```text
/mnt/pcs-usb/PCS-Share → /srv/pcs-share-backup
```

The sync script updates:

```text
/srv/pcs-share-backup/LAST_SYNC.txt
```

Warning: this is a mirror-style sync. Files deleted from the USB primary share may also be deleted from the SD backup.

## Safe USB Removal

Recommended terminal process:

```bash
./scripts/sync-pcs-share-to-backup.sh
sudo systemctl stop smbd
sudo umount /mnt/pcs-usb
sudo udisksctl power-off -b /dev/sda
sudo systemctl start smbd
```

The PCS web control panel also includes a safe USB unmount action:

```text
http://10.42.0.1
```

## Windows Access

From Windows File Explorer:

```text
\\10.42.0.1\PCS-Share
\\10.42.0.1\PCS-Backup
```

If Windows asks for credentials, use the Pi/Samba user account.

Example:

```text
Username: pi
Password: Pi/Samba password
```

## Notes

- `PCS-Share` is the working field share.
- `PCS-Backup` is the SD-card backup mirror.
- The current USB stick is formatted as `vfat`, so Linux ownership and permission behavior is limited compared to `ext4`.
- Samba still presents the share normally to network clients.
- Future builds may use a different USB UUID, device path, or filesystem.
