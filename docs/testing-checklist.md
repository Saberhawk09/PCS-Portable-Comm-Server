# Testing Checklist

This checklist validates the current PCS build after setup, reboot, or major changes.

## Pi-Side Quick Test

From the repository root:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/pcs-self-test.sh
```

Expected:

```text
PCS Pi-side self-test PASSED.
Fail: 0
Warn: 0
```

Warnings may appear if the cellular profile has not been manually activated yet.

Then run:

```bash
./scripts/pcs-status.sh
```

Review the output for service, network, storage, modem, GPS, Chrony, and share status.

## Git State

From the repo root:

```bash
git status
```

Expected:

```text
nothing to commit, working tree clean
```

## Reboot Test

Reboot:

```bash
sudo reboot
```

After the Pi comes back:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/pcs-self-test.sh
./scripts/pcs-status.sh
```

Expected:

```text
PCS Pi-side self-test PASSED.
```

PCS should come back without manual service startup.

## Network Test From Pi

Check addresses:

```bash
ip addr
ip route
```

Expected PCS LAN address:

```text
10.42.0.1/24
```

Ping OpenWrt AP:

```bash
ping -c 4 10.42.0.2
```

Expected:

```text
10.42.0.2 replies
```

Check neighbor table:

```bash
ip neigh show dev eth0
```

Stale entries can be cleared with:

```bash
sudo ip neigh flush dev eth0
```

## Windows Client Network Test

Connect a Windows client to the PCS Wi-Fi or Ethernet.

Run:

```cmd
ipconfig
```

Expected:

```text
IPv4 Address:      10.42.0.x
Subnet Mask:       255.255.255.0
Default Gateway:   10.42.0.1
DNS Server:        10.42.0.1
```

Ping PCS:

```cmd
ping 10.42.0.1
```

Expected:

```text
Reply from 10.42.0.1
```

Ping OpenWrt AP:

```cmd
ping 10.42.0.2
```

Expected:

```text
Reply from 10.42.0.2
```

## Internet Test

Only test this when an uplink is expected to be active.

From Windows:

```cmd
ping 8.8.8.8
ping google.com
```

Expected with active uplink:

```text
8.8.8.8 replies
google.com resolves and replies
```

If cellular data is disconnected, internet tests may fail. That does not mean PCS LAN services are broken.

## Dashboard / Control Panel Test

From a PCS client browser:

```text
http://10.42.0.1
```

Expected:

```text
Redirects to PCS Control Panel
```

Direct Control Panel URL:

```text
http://10.42.0.1:8080
```

Expected:

```text
PCS Control Panel loads
```

Cockpit:

```text
https://10.42.0.1:9090
```

Expected:

```text
Cockpit login page loads
```

## Samba Share Test

From Windows File Explorer:

```text
\\10.42.0.1\PCS-Share
```

Expected:

```text
Primary share opens
```

Backup share:

```text
\\10.42.0.1\PCS-Backup
```

Expected:

```text
Backup share opens
```

Command Prompt:

```cmd
net view \\10.42.0.1
```

Expected:

```text
PCS-Share
PCS-Backup
```

## File Write Test

On a Windows client:

1. Open `\\10.42.0.1\PCS-Share`
2. Create a test file
3. Edit the test file
4. Save it
5. Delete it

Expected:

```text
Create, edit, save, and delete all work
```

## Backup Sync Test

Create a test file in:

```text
\\10.42.0.1\PCS-Share
```

On the Pi:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/sync-pcs-share-to-backup.sh
```

Then check:

```text
\\10.42.0.1\PCS-Backup
```

Expected:

```text
The test file appears in PCS-Backup
```

## Chrony / NTP Test

On the Pi:

```bash
chronyc sources -v
chronyc tracking
```

Expected:

```text
Chrony is running and serving time
```

From Windows:

```cmd
w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly
```

Expected:

```text
Replies from 10.42.0.1
```

## RTC Test

On the Pi:

```bash
ls /dev/rtc*
timedatectl
dmesg | grep -i rtc
```

Expected:

```text
RTC device exists
System time is sane after reboot
```

## GPSD Test

On the Pi:

```bash
systemctl status gpsd
```

If GPS tools are installed:

```bash
cgps
```

Expected when GPS is working:

```text
gpsd is running
NMEA data is received
Satellite/fix data appears when antenna has sky view
```

Raw NMEA test:

```bash
gpspipe -r -n 40
```

Expected valid-fix indicators:

```text
$GPRMC,...,A,...
$GPGGA,...,1,...
```

If NMEA appears but there is no valid fix, check the active GPS antenna path:

```text
GPS SMA bias: about 3.1-3.3 VDC
MHF4 GPS pigtail: seated, correct connector, DC-continuous
AT+WANT: enabled for GNSS antenna power
GPSSEL/RF path: matches the SMA being used
```

## Modem Detection Test

On the Pi:

```bash
mmcli -L
```

Expected:

```text
A Sierra Wireless / Semtech modem is listed
```

Inspect modem:

```bash
mmcli -m 0
```

If multiple modems or modem numbers are present, adjust the modem number.

## Cellular Manual Control Test

Open:

```text
http://10.42.0.1:8080
```

Use the PCS Control Panel to manually connect cellular data.

Then test:

```bash
ip route
ping -c 4 8.8.8.8
ping -c 4 google.com
```

Expected when connected:

```text
Default route through active uplink
8.8.8.8 replies
DNS resolves
```

Disconnect cellular data from the Control Panel when finished.

## OpenWrt AP Test

From a client:

```text
http://10.42.0.2
```

Expected:

```text
OpenWrt management page loads
```

Confirm:

```text
DHCP disabled on OpenWrt
LAN IP is 10.42.0.2
Gateway/DNS point to 10.42.0.1 if configured
```

## Service Status Test

On the Pi:

```bash
systemctl status smbd
systemctl status chrony
systemctl status gpsd
systemctl status cockpit
```

If PCS services are installed:

```bash
systemctl list-units | grep pcs
```

Expected:

```text
Required services are active or intentionally inactive
```

## Full Field Test

Before using PCS for an event:

- [ ] PCS boots cleanly
- [ ] Pi self-test passes
- [ ] OpenWrt AP reachable at `10.42.0.2`
- [ ] Client receives `10.42.0.x` address
- [ ] Client can ping `10.42.0.1`
- [ ] Client can open Control Panel
- [ ] Client can open `PCS-Share`
- [ ] Client can write to `PCS-Share`
- [ ] Backup sync works
- [ ] Client can access `PCS-Backup`
- [ ] Windows NTP test works
- [ ] GPS source appears in Chrony when antenna has sky view
- [ ] Cellular can be manually connected if needed
- [ ] Cellular can be manually disconnected
- [ ] System survives reboot and returns to working state

## Pass Criteria

PCS is ready for field use when:

```text
Pi self-test passes
Control Panel loads
Clients receive PCS LAN addresses
Samba shares work
NTP works
Backup sync works
OpenWrt AP is reachable
Internet works when uplink is intentionally active
```
