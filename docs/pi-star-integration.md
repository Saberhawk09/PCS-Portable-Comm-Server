# Pi-Star Integration

PCS includes a Pi-Star hotspot as a fixed-address field-LAN node.

```text
PCS server:      10.42.0.1
OpenWrt AP:      10.42.0.2
Pi-Star hotspot: 10.42.0.3
DHCP clients:    10.42.0.100-10.42.0.200
```

The tested hotspot is Pi-Star 4.2.3 on Raspbian 11 with hostname `pcs-hotspot`.
Its installed Realtek RTL8152 USB Ethernet adapter is `eth0`; onboard Wi-Fi is
disabled after a guarded wired handoff.
For a complete rebuild sequence, start with
[Full-Stack Reinstall Runbook](full-stack-reinstall.md).

During PCS base setup, answer yes to:

```text
Include a Pi-Star hotspot in PCS monitoring and local-access links?
```

This records `PCS_SETUP_PISTAR=yes` in the private install configuration.
Builds without Pi-Star should select no; Pi-Star fields and checks are then
omitted instead of creating a persistent health warning.

## Automated Setup

Copy `scripts/setup-pistar-pcs.sh` from this repository to Pi-Star, then run it
as the normal Pi-Star user:

```bash
chmod +x ./setup-pistar-pcs.sh
PCS_PISTAR_DISABLE_WIFI=no ./setup-pistar-pcs.sh --apply
sudo reboot
```

This first stage moves `10.42.0.3/24` to the USB Ethernet adapter while leaving
Wi-Fi available as a recovery path. The installer refuses to write anything
unless `eth0` is USB-backed, uses the expected `r8152` driver, has carrier, and
can already reach PCS at `10.42.0.1`.

After the first reboot, confirm that `.3` is on wired `eth0`:

```bash
PCS_PISTAR_DISABLE_WIFI=no ./setup-pistar-pcs.sh --check
```

Only after that check passes, apply the final as-built profile and reboot again:

```bash
./setup-pistar-pcs.sh --apply
sudo reboot
```

Final verification:

```bash
./setup-pistar-pcs.sh --check
```

The script supports the tested `dhcpcd`-based Pi-Star image, owns a clearly
marked block in `/etc/dhcpcd.conf`, and preserves Pi-Star's normally read-only
root and boot filesystem states. The final profile adds a managed
`dtoverlay=disable-wifi` block to `/boot/config.txt`; it does not erase stored
Wi-Fi credentials. Environment variables can override the documented defaults;
run the script without `sudo` so its scoped sudo operations and backup behavior
remain intact.

The script does not configure or erase the PCS Wi-Fi password, callsign, radio
modes, or digital-network credentials. Restore those with Pi-Star's native
backup or enter them through its dashboard. For physical recovery if wired
Ethernet is unavailable, remove only the managed `PCS HOTSPOT WIFI` block from
the SD card's `/boot/config.txt`; the preserved credentials can then bring
Wi-Fi back through normal Pi-Star behavior.

## Coordinated Shutdown

PCS can request a clean Pi-Star shutdown before powering itself off. Run this
on PCS after Pi-Star is reachable at `10.42.0.3`:

```bash
cd /home/pi/Projects/PCS-Portable-Comm-Server
./scripts/setup-pistar-shutdown.sh --apply
```

SSH asks for the Pi-Star password once. The setup script uses it only to
bootstrap a dedicated SSH key and does not save the password. The installed
key is restricted on Pi-Star to readiness checks and poweroff; it cannot open a
shell or execute arbitrary commands.

Non-disruptive readiness test:

```bash
./scripts/setup-pistar-shutdown.sh --check
```

When pairing is present, the PCS dashboard's **Shutdown PCS** action requests
Pi-Star poweroff first and then powers off PCS. An offline or unpaired Pi-Star
does not block PCS shutdown.

## Time

Pi-Star uses the PCS server as its preferred NTP source and retains public NTP fallback:

```ini
# /etc/systemd/timesyncd.conf.d/50-pcs.conf
[Time]
NTP=10.42.0.1
FallbackNTP=0.debian.pool.ntp.org 1.debian.pool.ntp.org
```

Verification:

```bash
timedatectl timesync-status
```

The expected server is `10.42.0.1`.

## GPS

The tested network integration gives Pi-Star's YSFGateway live positioning
from the PCS GPS subsystem. This supports YSFGateway's GPS/APRS behavior
without attaching another receiver to the hotspot.

Pi-Star 4.2.3 includes two different GPS paths:

- The legacy `MobileGPS` service reads a GPS receiver attached to Pi-Star's local serial port and binds UDP port 7834.
- YSFGateway includes a native GPSD client.

The PCS WWAN modem is already connected to GPSD on the PCS server. Use YSFGateway's native GPSD client instead of forwarding raw NMEA to UDP port 7834.

On PCS:

```bash
bash ./scripts/setup-gpsd-lan-proxy.sh
```

This publishes a LAN-only proxy at `10.42.0.1:2947` while leaving GPSD itself on localhost.

See [GPS Network Sharing](gps-network-sharing.md) for the complete PCS data
path and examples for other GPSD or raw-NMEA consumers.

The setup script above is the repeatable method. For reference, the equivalent
manual GPS-only change begins by remounting the normally read-only root
filesystem and backing up the configuration:

```bash
sudo mount -o remount,rw /
sudo cp -a /etc/ysfgateway "/etc/ysfgateway.backup-$(date +%Y%m%d-%H%M%S)"
sudoedit /etc/ysfgateway
```

Configure:

```ini
# /etc/ysfgateway
[GPSD]
Enable=1
Address=10.42.0.1
Port=2947
```

After saving the file, run:

```bash
sync
sudo mount -o remount,ro /
sudo systemctl restart ysfgateway
```

YSFGateway may open GPSD only when it needs a location update, so an idle
connection is not a reliable health check. Verify the coordinate-free GPSD
protocol response from Pi-Star:

```bash
python3 - <<'PY'
import json
import socket

with socket.create_connection(("10.42.0.1", 2947), timeout=5) as client:
    client.sendall(b"?VERSION;\n")
    reply = client.recv(4096).decode("ascii", "replace")

version = json.loads(reply.splitlines()[0])
print(version["class"], version["release"])
PY
```

The response class should be `VERSION`. Leave Pi-Star's `mobilegps.service`
and legacy `[Mobile GPS]` block disabled when there is no receiver physically
attached to the hotspot.

## Feature Scope and Local-Receiver Comparison

YSFGateway can use this GPSD feed for its supported GPS/APRS behavior. The installed MMDVMHost and YSF2DMR binaries retain their own static `[Info]` location fields; do not claim that this path dynamically rewrites those fields or BrandMeister's hotspot location without separate verification.

For the verified YSFGateway use case, the network GPSD feed avoids a second GPS
receiver and provides the same PCS position source used by other LAN devices.
It also keeps antenna placement, GNSS acquisition, and time discipline
centralized on PCS.

A receiver physically attached to Pi-Star can use Pi-Star's local-serial
MobileGPS path. Depending on mode and image support, that path may feed
MMDVMHost features that do not consume YSFGateway's GPSD client. The current
network integration has not been verified as a general dynamic-location source
for DMR/BrandMeister, YSF2DMR, or every MMDVMHost mode. Keep static location
fields correct for those services, and use a local receiver if a required mode
specifically depends on MobileGPS.

The network method also depends on PCS, the AP, and the LAN being available.
A directly attached receiver can remain independent when Pi-Star is used away
from PCS. Those are the meaningful feature and failure-domain differences; GPS
fix quality itself depends primarily on the receiver, antenna, and view of the
sky.
