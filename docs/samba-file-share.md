# Samba File Share

PCS will provide a LAN-accessible file share for client PCs.

The final file share is expected to use removable storage, but a temporary test share has already been validated using local Pi storage.

## Temporary Test Share

The temporary test share is created by:

    ./scripts/setup-test-samba-share.sh

Default test share details:

- Share name: `PCS-Share`
- Local path: `/srv/pcs-share`
- Access method: Samba user login
- Tested client: Windows PC

## Confirmed Working

Confirmed on 2026-07-01:

- Samba installed successfully
- Temporary share directory created at `/srv/pcs-share`
- Share `PCS-Share` visible from Windows
- Windows client login successful
- Windows client could access the share

## Windows Access

From Windows File Explorer:

    \\pcs-pi.local\PCS-Share

If `.local` does not resolve, use the Pi IP address:

    \\<pi-ip-address>\PCS-Share

The Pi IP address can be checked with:

    hostname -I

## Credentials

Use the Samba username/password configured by:

    sudo smbpasswd -a <username>

Do not commit Samba passwords or real credentials to the repository.

## Final Share Plan

The final PCS file share should eventually move from `/srv/pcs-share` to removable storage.

Pending final setup tasks:

- Choose final external storage device
- Decide mount point
- Configure persistent mounting
- Configure final Samba share path
- Test read/write access from Windows clients
- Document recovery/rebuild process
