# PCS APRS Agent

The PCS APRS Agent is a small, read-only APRS message application layered on
Dire Wolf. Dire Wolf remains the only APRS-IS client and continues to own RF
audio, PTT, beaconing, digipeating, IGate policy, and packet routing.

## Data path and safety boundary

```text
APRS-IS <-> Dire Wolf IGate <-> KISS ICHANNEL 8 <-> pcs-aprs-agent
RF      <-> Dire Wolf radio channel 0
```

`ICHANNEL 8` is a Dire Wolf virtual Internet channel. Frames submitted by the
agent on channel 8 go to APRS-IS, not the radio; channel 0 remains RF. The agent rejects KISS channel
0 in configuration and ignores every received KISS channel except the selected
Internet channel. It connects only to loopback and never has an APRS-IS
passcode.

Dire Wolf's existing KISS TCP listener is unauthenticated and admitted from the
trusted PCS LAN by its nftables policy. Enabling ICHANNEL therefore also lets a
trusted-LAN KISS client select that Internet channel. The agent itself uses
loopback only, but the PCS LAN remains an explicit administrative trust
boundary; untrusted clients must not be admitted to it.

Stopping or crashing `pcs-aprs-agent.service` does not stop Dire Wolf. The unit
has ordering only; it has no `Requires`, `Wants`, `PartOf`, or service-control
permissions affecting Dire Wolf.

The agent is not compatible with Graywolf's KISS listener merely because it
uses the same TCP port. Installation requires `PCS_APRS_ENGINE="direwolf"` and
an active `direwolf.service`. At runtime the agent checks that unit before every
connection attempt, so an engine switch or stopped Dire Wolf cannot make it
reconnect to another service on TCP 8001. The commissioned PCS currently uses
Dire Wolf; Graywolf remains unsupported by this agent and must stay inactive.

## Message behavior

The agent accepts only correctly formed APRS messages whose fixed-width
addressee resolves exactly to `W8IJC-10` and which carry a one-to-five character
message ID. It immediately sends `ack<ID>` through ICHANNEL 8.

Received `(sender, message ID)` identities and a SHA-256 body digest are retained
in SQLite for 24 hours. Duplicate packets are ACKed again but their commands are
not run again. Reuse of the same sender/ID with a different body is ACKed, logged
as a conflict, and not executed. The database never stores command bodies,
status responses, GPS coordinates, APRS-IS credentials, or radio settings.

Status replies receive a persistent four-character outbound message ID. ACK
tracking and retry of those replies are intentionally deferred to a later
version.

## Read-only commands

| Command | Reply |
| --- | --- |
| `PING` | `PONG` |
| `STATUS` | Compact PCS/LTE/GPS/power/temperature/network summary |
| `POWER` | `POWER N/A` until monitoring hardware is installed |
| `LTE` | `UP`, `STANDBY`, `NO MODEM`, or `UNKNOWN` |
| `GPS` | `3D`, `2D`, `STALE`, `NO FIX`, or `UNAVAILABLE` |
| `TEMP` | Raspberry Pi thermal-zone temperature |
| `NET` | Coarse active transport: Ethernet, Wi-Fi, cellular, up, or down |
| `UPTIME` | Days/hours/minutes from `/proc/uptime` |
| `HELP` | Supported command names |

GPS replies never include coordinates. LTE and network replies never expose IP
addresses, SSIDs, carriers, IMEI/ICCID values, device names, or account data.
All subprocesses use fixed read-only argument lists with bounded timeouts; APRS
text is never passed to a shell. Per-sender and global command/reply limits
bound abuse of the public, unauthenticated APRS identity. Protocol ACKs are sent
before those limits so valid numbered duplicates are still ACKed without
re-running their commands.

## Configuration

The versioned source template is `config/aprs-agent.example.conf`. Deployment
generates `/etc/pcs/aprs-agent.conf` from the non-secret PCS install settings:

| Setting | Local default | Purpose |
| --- | --- | --- |
| `PCS_APRS_AGENT_ENABLED` | `no` | Explicitly opt in to generating the Internet channel and permitting installation. |
| `PCS_APRS_AGENT_ICHANNEL` | `8` | Unused KISS port nibble mapped to APRS-IS. Must never overlap radio channels. |
| `PCS_APRS_AGENT_TOCALL` | `APZPCS` | Experimental PCS software destination identifier. |
| `PCS_APRS_AGENT_DEDUPE_TTL_SECONDS` | `86400` | Persistent duplicate window. |
| `PCS_APRS_AGENT_SENDER_RATE_PER_MINUTE` | `12` | Per-sender command/reply ceiling; required ACKs are not dropped. |
| `PCS_APRS_AGENT_GLOBAL_RATE_PER_MINUTE` | `60` | Whole-agent command/reply ceiling; required ACKs are not dropped. |

## Local validation and later deployment

Local development requires no radio, APRS-IS connection, or PCS appliance:

```bash
python3 -m unittest tests.test_aprs_agent -v
python3 -m py_compile scripts/pcs_aprs_agent.py
bash -n scripts/setup-pcs-aprs-agent.sh
```

When deployment is separately approved, first render and validate the managed
Dire Wolf profile so it contains the matching `KISSPORT` and `ICHANNEL`. Then:

```bash
./scripts/setup-pcs-aprs-agent.sh --install
journalctl -u pcs-aprs-agent.service
```

The installer refuses to start the agent unless Dire Wolf is selected and
active and the live managed Dire Wolf file already contains both matching
directives. It does not edit or restart Dire Wolf, collect credentials, touch
GPIO/PTT/audio, or transmit RF.
