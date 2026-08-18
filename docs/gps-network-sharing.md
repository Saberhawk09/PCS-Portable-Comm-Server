# GPS Network Sharing

PCS can share the WWAN modem's GNSS data with trusted devices on the field LAN.
The preferred path keeps GPSD private on the PCS server and publishes a second
listener only on the PCS LAN address.

```text
EM7565 GNSS
    |
    | NMEA on /dev/ttyUSB1
    v
gpsd on 127.0.0.1:2947
    |
    +-- Dire Wolf GPS tracker (`GPSD localhost 2947`)
    +-- Chrony SHM time source
    |
    v
LAN-only GPSD proxy on 10.42.0.1:2947
    |
    +-- Pi-Star YSFGateway
    +-- Linux GPSD clients
    +-- Mapping and logging applications
    +-- Raw-NMEA adapters for devices that need NMEA sentences
```

Live position data is sensitive. Do not add `-G` to GPSD merely to share it
with another PCS device: that makes GPSD listen on every available interface,
including an uplink. Use the LAN-only proxy instead.

Dire Wolf runs locally and connects directly to `localhost:2947`. It must not
loop back through the LAN proxy. This keeps the APRS tracker independent of the
optional LAN-sharing socket while still allowing gpsd to arbitrate the single
`/dev/ttyUSB1` NMEA source.

## Enable the PCS LAN Interface

From the PCS repository on the server:

```bash
bash ./scripts/setup-gpsd-lan-proxy.sh
```

The installer:

- leaves GPSD listening on `127.0.0.1:2947`
- publishes `10.42.0.1:2947` with `systemd-socket-proxyd`
- enables the proxy socket at boot
- rejects an existing wildcard GPSD listener
- performs a coordinate-free GPSD protocol check

Check the installed listeners:

```bash
sudo ss -lntp | grep ':2947'
```

Expected bindings:

```text
127.0.0.1:2947   gpsd
10.42.0.1:2947   pcs-gpsd-lan.socket
```

There should be no `0.0.0.0:2947` or `[::]:2947` listener.

## Native GPSD Clients

Configure a native GPSD client with:

```text
Host: 10.42.0.1
Port: 2947
```

On a Linux client with `gpsd-clients`, display NMEA sentences generated from
the shared GPSD feed:

```bash
gpspipe -r -B 10.42.0.1:2947
```

Display GPSD JSON instead:

```bash
gpspipe -w -B 10.42.0.1:2947
```

Both commands include live coordinates. Do not save or share their output
unless that is intentional.

## Raw NMEA Consumers

Prefer a device's native GPSD support when available. If a device explicitly
requires raw NMEA over UDP, bridge the local PCS GPSD feed to that device:

```bash
gpspipe -r -B 127.0.0.1:2947 | nc -u DEVICE_IP DEVICE_PORT
```

Replace `DEVICE_IP` and `DEVICE_PORT` with the receiver's documented NMEA
endpoint. This command runs in the foreground and stops with `Ctrl+C`.

Do not assume that an arbitrary UDP port accepts raw NMEA. Confirm the
receiving application's protocol first. In particular, UDP port 7834 on the
tested Pi-Star 4.2.3 image belongs to its local-serial MobileGPS integration
and is not a general network NMEA input.

## Pi-Star

The tested Pi-Star integration uses YSFGateway's native GPSD client:

```ini
[GPSD]
Enable=1
Address=10.42.0.1
Port=2947
```

This supplies the PCS position to YSFGateway's supported GPS/APRS path without
a second receiver on the hotspot. See [Pi-Star Integration](pi-star-integration.md)
for the repeatable setup script, complete configuration, and
time-synchronization steps.

Pi-Star's legacy `mobilegps.service` remains disabled when no GPS receiver is
physically attached to the hotspot. The GPSD path does not dynamically rewrite
MMDVMHost or YSF2DMR static location fields.

## Coordinate-Free Connectivity Test

Use this from a trusted client when the goal is to test the service without
printing the current position:

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

The response class should be `VERSION`.
