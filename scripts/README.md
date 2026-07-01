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
