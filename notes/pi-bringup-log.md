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

Next planned checks:

- Confirm Git workflow from the Pi
- Detect WWAN modem over USB
- Install and test ModemManager
- Install and test Samba file sharing
- Install and test GPSD
- Install and test Chrony
