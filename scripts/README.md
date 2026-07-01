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
