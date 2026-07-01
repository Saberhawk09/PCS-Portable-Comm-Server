# Testing Checklist

This checklist tracks basic validation tests for PCS hardware, networking, and server functions.

## Power Tests

- [ ] AC input powers internal 12V PSU
- [ ] DC Anderson input powers internal 12V rail
- [ ] Source selector switch correctly selects AC / Off / DC
- [ ] 12V rail fuse protects main DC bus
- [ ] 5V buck converter outputs stable voltage
- [ ] Raspberry Pi boots from 5V rail
- [ ] Router powers correctly from selected rail

## Network Tests

- [X] Router creates Wi-Fi network
- [X] Router DHCP assigns client IP addresses
- [ ] Wired client can connect over Ethernet
- [X] Wireless client can connect over Wi-Fi
- [X] Client devices can communicate on LAN

## Server Tests

- [X] Pi is reachable over network
- [X] SMB file share is visible to clients
- [X] Client PC can read files from share
- [X] Client PC can write files to share
- [ ] GPSD detects GPS source
- [ ] Chrony uses GPS source for time discipline
- [ ] Client PC can use PCS as NTP server

## Cellular Tests

- [ ] WWAN modem is detected over USB
- [ ] LTE antennas are connected
- [ ] Cellular connection comes online
- [ ] Pi has internet access over cellular
- [X] Router receives WAN connection from Pi
- [X] Client PC can access internet through PCS

## Field Usability Tests

- [ ] All external ports are accessible
- [ ] Switch positions are clearly labeled
- [ git add docs/testing-checklist.md
git commit -m "Update testing checklist for router WAN handoff"
git push] System can be operated without opening enclosure
- [ ] Cooling is adequate under normal load
- [ ] File share works with expected logging software
