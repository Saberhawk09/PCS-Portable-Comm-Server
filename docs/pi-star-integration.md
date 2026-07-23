# Pi-Star Integration

PCS includes a Pi-Star hotspot as a fixed-address field-LAN node.

```text
PCS server:      10.42.0.1
OpenWrt AP:      10.42.0.2
Pi-Star hotspot: 10.42.0.3
DHCP clients:    10.42.0.100-10.42.0.200
```

The tested hotspot is Pi-Star 4.2.3 on Raspbian 11 with hostname `pcs-hotspot`.

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

On Pi-Star, remount the normally read-only root filesystem and back up the
configuration:

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

## Scope

YSFGateway can use this GPSD feed for its supported GPS/APRS behavior. The installed MMDVMHost and YSF2DMR binaries retain their own static `[Info]` location fields; do not claim that this path dynamically rewrites those fields or BrandMeister's hotspot location without separate verification.
