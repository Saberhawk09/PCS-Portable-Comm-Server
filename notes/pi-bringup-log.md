# Raspberry Pi Bring-Up Log

This file tracks Raspberry Pi setup, testing, and hardware bring-up notes for PCS.

## 2026-07-01

Fresh Raspberry Pi OS with desktop, 64-bit, installed for PCS testing.

Confirmed working:

- Raspberry Pi Connect remote shell
- Raspberry Pi Connect screen sharing
- Local SSH
- I2C RTC detected as /dev/rtc0
- DS1307-compatible RTC driver loaded
- System time set from RTC
- Timezone set to America/New_York
- NTP service active
- Basic utility packages installed
- Project repo cloned to ~/Projects/PCS-Portable-Comm-Server
- Dependency install script tested successfully
- NetworkManager active
- ModemManager active
- Avahi active as pcs-pi.local
- Chrony active and synced
- Cockpit working from Windows at port 9090
- Samba test share setup script tested successfully
- PCS-Share accessible from Windows
- Windows client login to Samba share confirmed
- Router WAN handoff test succeeded
- Pi shared current Wi-Fi uplink out through Ethernet
- Windstream test router received internet through Pi Ethernet
- Windows PC connected to test router Wi-Fi successfully reached internet
- Windows client received address `192.168.254.64` from Windstream router
- Windows client gateway was `192.168.254.254`
- Windows client successfully pinged `8.8.8.8`
- Windows client successfully resolved and pinged `google.com`

RTC verification:

- /dev/rtc exists
- /dev/rtc0 exists
- rtc-ds1307 registered as rtc0
- System clock was set from RTC during boot

Raspberry Pi Connect verification:

- Wayland compositor available
- Screen sharing services enabled and active
- Communication with Raspberry Pi Connect services working

Current notes:

- RTC overlay is enabled in /boot/firmware/config.txt
- Working config backed up as /boot/firmware/config.txt.pcs-rtc-working
- Avoid changing display/HDMI settings unless Raspberry Pi Connect screen sharing breaks again
- The Pi is currently being used as the PCS test system after backing up the previous GOES receiver SD card image
- eth0 currently shows unavailable because Ethernet is not connected
- wlan0 is connected through NetworkManager
- Temporary Samba share path is /srv/pcs-share
- Samba share name is PCS-Share
- Final share location may move to removable storage later
- Router WAN handoff currently uses Pi Wi-Fi as temporary uplink
- Future final uplink is expected to be the cellular modem
- Pi Ethernet share profile is `pcs-router-wan-share`
- Pi Ethernet share address is `10.42.0.1/24`

Next planned checks:

- Confirm Git workflow from the Pi
- Detect WWAN modem over USB
- Install and test ModemManager
- Install and test Samba file sharing
- Install and test GPSD
- Install and test Chrony
