# Scripts

This folder contains setup and diagnostic scripts for PCS.

## install-dependencies.sh

Installs baseline PCS software dependencies on a fresh Raspberry Pi OS install.

Run from the repository root:

    ./scripts/install-dependencies.sh

Installs packages for:

- General system utilities
- Hardware inspection
- NetworkManager
- ModemManager
- QMI / MBIM modem tools
- Samba / SMB
- GPSD
- Chrony
- Cockpit
- WireGuard tools

This script does not configure routing, Samba shares, GPSD, Chrony GPS sources, or modem profiles.

## setup-pcs-base.sh

Runs the full PCS baseline setup workflow on a fresh Raspberry Pi OS install.

Run from the repository root:

    ./scripts/setup-pcs-base.sh

This script runs:

- Dependency installation
- RTC setup
- Router WAN handoff setup
- Temporary Samba test share setup
- Chrony LAN NTP setup
- Cockpit/systemd restart button installation
- PCS status script
- PCS self-test script

This script does not configure:

- WWAN/cellular modem connection
- GPS/GNSS time source
- Final removable-storage Samba share

Those require hardware that may not be installed yet.

After setup, recommended validation is:

    sudo reboot
    cd /home/pi/Projects/PCS-Portable-Comm-Server
    ./scripts/pcs-self-test.sh
    ./scripts/pcs-status.sh

## setup-rtc.sh

Configures support for the PCS I2C RTC module.

Run from the repository root:

    ./scripts/setup-rtc.sh

The script:

- Backs up `/boot/firmware/config.txt`
- Enables I2C if needed
- Adds the DS1307-compatible RTC overlay if needed
- Avoids modifying HDMI/display settings

Reboot after running this script on a fresh install.

Verify RTC operation with:

    ls /dev/rtc*
    dmesg | grep -i rtc
    timedatectl

## setup-test-samba-share.sh

Creates a temporary Samba test share for validating LAN file sharing.

Run from the repository root:

    ./scripts/setup-test-samba-share.sh

Default share details:

- Share name: `PCS-Share`
- Local path: `/srv/pcs-share`
- Access: local Linux/Samba user credentials

From Windows, try:

    \\pcs-pi.local\PCS-Share

Or use the Pi IP address:

    \\<pi-ip-address>\PCS-Share

This is a temporary proof-of-concept share. The final PCS file share may use removable storage.

## setup-samba-backup-share.sh

Creates the PCS SD-card backup Samba share.

Run from the repository root:

    ./scripts/setup-samba-backup-share.sh

Default backup share details:

- Share name: `PCS-Backup`
- Local path: `/srv/pcs-share-backup`
- Access: local Linux/Samba user credentials

From Windows, access it with:

    \\10.42.0.1\PCS-Backup

This share is intended to hold a mirror copy of the primary PCS file share.

## sync-pcs-share-to-backup.sh

Manually mirrors the primary PCS file share to the SD-card backup share.

Run from the repository root:

    ./scripts/sync-pcs-share-to-backup.sh

Default sync direction:

    /srv/pcs-share → /srv/pcs-share-backup

The sync uses `rsync --delete`, which means files deleted from the primary share will also be deleted from the backup mirror.

This is intentionally one-way:

- `PCS-Share` is the primary share
- `PCS-Backup` is the backup mirror

This avoids the complexity and risk of bidirectional sync.

## setup-router-wan-share.sh

Creates a NetworkManager shared Ethernet profile for testing router WAN handoff.

Run from the repository root:

    ./scripts/setup-router-wan-share.sh

Default profile details:

- Profile name: `pcs-router-wan-share`
- Interface: `eth0`
- Pi Ethernet address: `10.42.0.1/24`
- IPv4 mode: shared
- IPv6 mode: ignored

This allows the Pi to share its current uplink out through Ethernet.

Temporary test layout:

    Internet over Pi Wi-Fi → Pi eth0 → Router WAN → Router clients

Future PCS layout:

    Cellular modem → Pi → Pi eth0 → Router WAN → Router clients

To activate the profile after connecting the router WAN port to the Pi Ethernet port:

    sudo nmcli connection up pcs-router-wan-share

To disable it:

    sudo nmcli connection down pcs-router-wan-share

To delete the profile:

    sudo nmcli connection delete pcs-router-wan-share

This script does not configure the WWAN modem. It only prepares the Ethernet handoff side.

## pcs-status.sh

Prints a PCS system status report.

Run from the repository root:

    ./scripts/pcs-status.sh

Reports:

- Hostname
- OS version
- Hardware model
- Kernel version
- Uptime
- Time / NTP / RTC status
- NetworkManager devices
- IP addresses
- Routes
- DNS configuration
- USB devices
- I2C bus status
- ModemManager status
- Key service states
- Raspberry Pi Connect status

## setup-chrony-lan-ntp.sh

Configures Chrony to serve NTP to clients on the PCS router-side network.

Run from the repository root:

    ./scripts/setup-chrony-lan-ntp.sh

Default NTP server address for router-side clients:

    10.42.0.1

The script:

- Backs up `/etc/chrony/chrony.conf`
- Allows NTP clients on `10.42.0.0/24`
- Preserves or enables RTC synchronization through `rtcsync`
- Enables local fallback with `local stratum 10`
- Restarts Chrony

Test from Windows:

    w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly

This does not configure GPS/GNSS as a Chrony source yet.

## pcs-self-test.sh

Runs a Pi-side PCS validation test.

Run from the repository root:

    ./scripts/pcs-self-test.sh

Checks:

- Hostname and OS
- Git working tree status
- RTC presence
- NTP synchronization
- Chrony LAN NTP configuration
- NetworkManager device state
- Router WAN handoff profile
- Internet and DNS connectivity
- Samba service and share configuration
- Cockpit service
- Raspberry Pi Connect status
- ModemManager status
- GPSD placeholder status

This script only validates the Pi side. Client-side checks such as Windows file share access, Cockpit access, and `w32tm` NTP testing should still be tested separately.

## restart-pcs-services.sh

Restarts core PCS services and refreshes the router WAN handoff profile.

This script is intended to be used by the Cockpit/systemd button service:

    pcs-restart-services.service

Services handled:

- Samba / `smbd`
- Chrony
- ModemManager
- Avahi
- Router WAN handoff profile `pcs-router-wan-share`

The script intentionally does not restart NetworkManager or Cockpit itself, to avoid cutting off remote access during troubleshooting.

For a full validation check after using the restart button, run:

    ./scripts/pcs-self-test.sh
