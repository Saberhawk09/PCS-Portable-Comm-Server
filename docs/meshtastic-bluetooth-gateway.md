# Meshtastic USB/Bluetooth MQTT Gateway

PCS can maintain a continuous connection to one dedicated Meshtastic node and
provide that node with local or internet MQTT access. The initial target is a
RAK4631. USB serial is the deployed PCS transport; a BLE transport adapter is
also included for hosts whose systemd device isolation permits BlueZ GATT.

The persistent USB session, broker connection, MQTT client-proxy uplink, and
service restart have been hardware-validated on PCS. Mesh RF behavior and the
environmental sensor readings remain operator-validated steps.

The commissioned PCS profile uses `mqtt.neomesh.org`, encrypted primary-channel
uplink/downlink, and Meshtastic Client Proxy over the persistent USB connection.
The RAK4631 also has hourly map reporting enabled with location sharing opted in
at precision 15. Successful radio-proxy publishes to the NeoMesh broker were
validated on August 24, 2026; appearance on any particular public map frontend
remains a separate external-service check.

The Meshtastic page at `neome.sh/meshtastic/` embeds an MQTT coverage map that
uses a separate broker. PCS can mirror every radio-generated uplink envelope to
that map broker while retaining `mqtt.neomesh.org` as its primary broker. The
mirror is publish-only and topic-scoped to the public default-key `LongFast`
channel plus Meshtastic's explicit opt-in `/2/map/` reports: it creates no
public-map subscriptions, never mirrors private-channel topics, and cannot flood
the local RF channel with internet traffic. IJC1's GPSD-fed positions, its
opt-in firmware map reports, and LongFast packets heard over RF are mirrored;
firmware consent (`config_ok_to_mqtt`) remains authoritative for remote stations.
For IJC1 itself, the gateway synchronizes GPSD into the radio's supported fixed
position only when no radio position exists or PCS has moved at least 500 meters.
This lets firmware MapReport include the opted-in location without rewriting the
RAK's flash on every hourly position refresh.

## Architecture

```text
Meshtastic mesh
      |
      | LoRa
      v
RAK4631
      |
      | persistent USB serial or BLE PhoneAPI session
      v
pcs-meshtastic.service on PCS
      |
      | MQTT 3.1.1 (plain or TLS)
      v
local/private broker or internet broker
```

The service implements Meshtastic's transparent MQTT client-proxy protocol. It
does not decode and rebuild mesh messages. The RAK4631 supplies the exact MQTT
topic, binary/text payload, and retained flag for uplink. PCS publishes that
envelope unchanged. Messages received on explicitly allowed broker topics are
returned through Meshtastic's dedicated MQTT proxy API.

PCS does not call general message, owner, or channel write methods. Radio and
channel configuration remains on the RAK4631. The one narrow configuration
exception is the opted-in public-map position: when that mirror is enabled, PCS
uses Meshtastic's fixed-position API only for an absent or materially moved
GPSD fix. Normal refreshes remain ordinary Meshtastic position packets.

## PCS GPS Position Feed

Enable the position feed after the persistent gateway is configured:

```bash
./scripts/setup-meshtastic-bluetooth.sh --enable-gpsd-position
```

The gateway requires a valid 2D or 3D fix from `gpsd` and sends at most one
position update every 30 minutes on primary channel index 0. A missing fix is
skipped rather than replaced with zero or stale coordinates. The normal
Meshtastic channel and position-precision rules determine who can receive the
packet. PCS status records only update counts and timestamps, never coordinates.

Disable it with:

```bash
./scripts/setup-meshtastic-bluetooth.sh --disable-gpsd-position
```

Enable the separate uplink-only mirror used by the MQTT coverage map embedded
on the NeoMesh Meshtastic page:

```bash
./scripts/setup-meshtastic-bluetooth.sh --enable-neomesh-map
```

Enabling the mirror also sets primary-channel position precision to 15 bits
(approximately 729 meters) before saving the broker configuration. This prevents
ordinary LongFast position packets from overriding the same precision selected
for firmware MapReport. Disabling the mirror leaves that privacy-conservative
channel precision in place.

Disable only that mirror, without changing the primary NeoMesh broker:

```bash
./scripts/setup-meshtastic-bluetooth.sh --disable-neomesh-map
```

The RAK4631 is dedicated to PCS while this service is active. Stop the gateway
before using another serial client. In BLE mode, disconnect the Meshtastic
mobile app before starting PCS because normal peripherals accept one client.

## Safety and Privacy Boundaries

- MQTT downlink has no default subscription. Uplink can be commissioned first.
- Never use a broad public subscription such as `msh/#`. The public default
  channel can carry enough traffic to overload a small node. The gateway
  rejects multi-level `#` filters and permits `+` only as the final topic level.
- Configure only the exact encrypted channel and PKI filters required by this
  PCS installation.
- A short-lived hash cache suppresses an immediate broker echo of a packet PCS
  just published. Meshtastic firmware also marks MQTT-originated packets so it
  does not publish them back to MQTT.
- Runtime status stores aggregate counters and the local RAK4631 telemetry. It
  does not store message bodies, remote identities, positions, or channel keys.
- The gateway requests node-less startup from Meshtastic. It does not download
  the radio's historical remote-node database; live aggregate counts therefore
  build from traffic observed during the current daemon run.
- Broker credentials live in a root-only environment file. Do not put the MQTT
  password in a shell command, repository file, or `config/pcs-install.conf`.

## Software Staging

The base installer can stage the pinned dependencies without contacting a
radio or broker:

```bash
./scripts/setup-meshtastic-bluetooth.sh --prepare
```

Staging installs:

- Meshtastic Python `2.7.11`
- Paho MQTT `2.1.0`
- `/usr/local/sbin/pcs-meshtastic-gateway`
- `/usr/local/sbin/pcs_meshtastic_ble.py` (BlueZ startup compatibility adapter)
- `/usr/local/sbin/pcs_meshtastic_status.py` (gateway status helper)
- `pcs-bluetooth-ready.service` (unblocks and powers Bluetooth for the gateway)
- `pcs-meshtastic.service`

The service remains stopped and disabled in staged state.

For an installed PCS, refresh versioned gateway and systemd assets without
changing active/staged state or rewriting radio/MQTT configuration:

```bash
./scripts/setup-meshtastic-bluetooth.sh --refresh
```

An active gateway restarts only when a service-relevant managed file changed;
status-helper-only updates do not interrupt the radio session.

## RAK4631 Preparation

Use the Meshtastic mobile application or another supervised client before
giving the BLE connection to PCS:

1. Record the installed firmware version.
2. Confirm the correct LoRa region and channel configuration.
3. Enable Bluetooth and choose a pairing mode/PIN usable by the headless Pi.
4. Confirm the powered node will remain awake with Bluetooth available; an
   always-on gateway is incompatible with a radio power profile that disables
   BLE or enters deep sleep.
5. Enable the MQTT module.
6. Enable **Client Proxy**.
7. Configure the same broker address, credentials, root, encryption, and TLS
   policy that PCS will use.
8. Enable uplink and/or downlink only on the intended channels.
9. Prefer encrypted channels and a private broker for operational traffic.

For the commissioned PCS/NeoMesh node, the non-secret radio-side policy is:

- MQTT address `mqtt.neomesh.org`
- MQTT module and Client Proxy enabled
- MQTT encryption enabled
- primary channel uplink and downlink enabled
- map reporting and location opt-in enabled
- 3600-second map interval and position precision 15
- LoRa `config_ok_to_mqtt` enabled

Broker credentials and the encrypted channel key remain outside Git. The active
gateway status must report `radio_mqtt_enabled`, `radio_proxy_enabled`, and
`radio_broker_matches` as true; PCS self-test treats a mismatch as a failure.

The exact firmware version matters. Headless BLE pairing and client-proxy bugs
have existed in individual Meshtastic firmware releases, so a failure must not
be diagnosed as PCS hardware trouble until the installed RAK firmware and its
known issues are checked.

## Pairing on PCS

Pair interactively so the PIN is entered only at the terminal:

```bash
./scripts/setup-meshtastic-bluetooth.sh --scan
bluetoothctl
```

At the `bluetoothctl` prompt:

```text
power on
agent KeyboardDisplay
default-agent
scan on
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
scan off
quit
```

Replace the example address with the RAK4631 address reported by the scan. Do
not accept an unexpected device identity or pairing prompt.

PCS retains its global `rfkill default_state=0` hardening. The static
`pcs-bluetooth-ready.service` dependency unblocks and powers only the Bluetooth
controller when the Meshtastic gateway starts, so the persistent connection
also recovers after reboot.

Some headless RAK4631 firmware releases fail fixed- and random-PIN pairing
before BlueZ can present a passkey prompt. First disconnect other Meshtastic
clients and power-cycle the RAK, because a stale single-client BLE session can
look the same. If pairing still fails, use the mobile app or a one-time USB
serial connection to select a Bluetooth mode the headless Linux host can use.
Do not factory-reset the node merely to clear this condition: a full reset also
clears BLE bonds and other device state, and can rotate identity/security state.

## Broker Credentials

After staging, edit the root-only credential file:

```bash
sudoedit /etc/pcs/meshtastic-mqtt.env
```

```text
PCS_MESHTASTIC_MQTT_USERNAME="broker-user"
PCS_MESHTASTIC_MQTT_PASSWORD="broker-password"
```

Leave both values empty for a deliberately anonymous local broker. Keep the
file owned by `root:root` with mode `0600`.

When the radio already contains the intended broker credentials, PCS can copy
them over the supervised BLE connection without displaying them:

```bash
./scripts/setup-meshtastic-bluetooth.sh --import-radio-mqtt AA:BB:CC:DD:EE:FF
```

This changes only the root-only PCS credential file; it does not change or
print the radio's MQTT configuration.

## Start the Persistent Gateway

Configure the paired BLE target and broker hostname. Port `8883` automatically
selects TLS; other ports default to plaintext unless the environment file is
edited explicitly.

```bash
./scripts/setup-meshtastic-bluetooth.sh --configure AA:BB:CC:DD:EE:FF mqtt.example.net 8883
```

This starts and enables `pcs-meshtastic.service`. The service keeps the BLE
session open, retries BLE every 15 seconds after loss, and lets the MQTT client
reconnect with bounded backoff. BLE shutdown is also bounded so a BlueZ or
firmware disconnect stall cannot prevent a service restart or system shutdown.

For the deployed USB-connected RAK4631, use the scoped serial mode instead:

```bash
./scripts/setup-meshtastic-bluetooth.sh --configure-usb /dev/ttyACM0 mqtt.example.net 8883
```

The hardened unit retains `PrivateDevices=yes` and receives access only to
`/dev/ttyACM0`. USB mode preserves an existing explicit subscription allowlist
when configuration is refreshed. Runtime status reports `transport` as
`usb-serial`; the legacy `gateway.ble_connected` compatibility field means the
selected radio transport is connected in either mode.

`--configure-usb` also disables Bluetooth in the connected Meshtastic node.
This prevents the RAK from continuing to advertise its no-PIN management
interface when PCS is deployed in a busy environment. Re-enabling BLE later is
an explicit maintenance operation performed over USB before selecting BLE mode.

The installer also excludes the RAK4631 USB identity from ModemManager probing.
After a cold boot, a bounded readiness gate waits for the serial device and for
110 seconds of system uptime before the long-running gateway takes ownership.
It deliberately does not probe the port repeatedly while the nRF52 stack is
booting. The unit allows up to 150 seconds for this precheck; systemd does not
report the gateway active until the cold-boot delay has passed.

On this PCS/Raspberry Pi combination, the RAK4631 served a complete BLE
configuration stream outside the service sandbox, but `PrivateDevices=yes`
caused BlueZ to drop that GATT configuration session. Do not disable the entire
device sandbox merely to force BLE; use the scoped USB transport unless a
narrow BLE-safe systemd policy is validated later.

Inspect it with:

```bash
./scripts/setup-meshtastic-bluetooth.sh --check
/usr/local/sbin/pcs_meshtastic_status.py --check
systemctl status pcs-meshtastic.service --no-pager
journalctl -u pcs-meshtastic.service -n 100 --no-pager
```

The installed status command reads the gateway's existing privacy-safe JSON
snapshot. It does not open the USB/BLE transport or compete with the running
daemon. Direct one-shot BLE collection exists only behind the explicit
`--collect-ble` option and is not used by the dashboard.

## PCS Dashboard Integration

When the gateway is configured, the public PCS homepage includes a
**Meshtastic / MQTT** card alongside APRS. It reports the local node and
firmware, transport health, live MQTT state and broker, aggregate mesh/proxy
counters, recent-node counts, GPSD position-feed health, case environment,
LoRa utilization, power, radio/PCS broker agreement, the public-map policy, and
embedded-map mirror connection/uplink health.
Staged software remains visible only to an authenticated operator.

The authenticated dashboard adds service and status-freshness diagnostics plus
two fixed actions: **View Meshtastic** and confirmed **Restart Meshtastic**.
Restart reconnects the existing radio and broker configuration; it does not
rewrite the radio, channels, credentials, subscriptions, or public-map policy.

No dashboard view receives MQTT credentials, subscription topic strings,
channel keys, message content, remote identities, or stored position
coordinates. Topic filters remain represented only by a count.

## Enable Controlled Downlink

Commission uplink first and determine the exact encrypted channel topic emitted
by the RAK4631. Then edit `/etc/pcs/meshtastic.env` and set a comma-separated
allowlist, for example:

```text
PCS_MESHTASTIC_MQTT_SUBSCRIPTIONS=msh/US/2/e/CHANNEL_ID/+,msh/US/2/e/PKI/+
```

`CHANNEL_ID` is an example placeholder, not the channel's display name or
secret key. Use the actual topic produced for this RAK and broker root. Restart
the service after changing filters:

```bash
sudo systemctl restart pcs-meshtastic.service
```

## PCS Case Environment Telemetry

When Meshtastic recognizes the local RAK4631's attached temperature/humidity
sensor and environment measurement is enabled, the gateway status includes:

```json
{
  "case_environment": {
    "source": "local-meshtastic-node",
    "temperature_c": 29.5,
    "temperature_f": 85.1,
    "humidity_percent": 44.25,
    "sampled_at": "2026-08-20T01:23:45+00:00",
    "sample_age_seconds": 10
  }
}
```

Only the locally attached RAK4631 is used. Remote-node sensor readings are not
mistaken for the PCS enclosure. In the Meshtastic app, enable **Environment
Measurement** under the Telemetry module and select a reasonable environment
update interval. Short intervals consume more mesh airtime and battery.

Treat this as an internal-case trend sensor until its placement is finalized
and its readings are compared with a known reference. Board self-heating and
restricted airflow can make it read differently from case inlet or ambient
temperature.

## Disable Without Changing the Radio

```bash
./scripts/setup-meshtastic-bluetooth.sh --disable
```

This stops and disables the PCS gateway and records software-staged state. It
does not change the RAK4631 MQTT, channel, telemetry, or RF configuration.
