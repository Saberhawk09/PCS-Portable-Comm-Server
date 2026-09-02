# PCS APRS Agent

The PCS APRS Agent is a small status-command and message-mailbox application
layered on Dire Wolf. Dire Wolf remains the only APRS-IS client and continues to own RF
audio, PTT, beaconing, digipeating, IGate policy, and packet routing.

## Data path and safety boundary

```text
APRS-IS <-> Dire Wolf IGate <-> KISS ICHANNEL 8 <-> pcs-aprs-agent
144.550 <-> Dire Wolf radio channel 0 <-> pcs-aprs-agent (explicit opt-in)
```

`ICHANNEL 8` is a Dire Wolf virtual Internet channel. Frames submitted by the
agent on channel 8 go to APRS-IS, not the radio. When the separately gated RF
option is enabled, the agent also accepts physical radio channel 0 and returns
its ACK and reply on the same channel on which a request arrived. A local
144.550 MHz request therefore receives a direct 144.550 MHz response, while an
APRS-IS request remains on ICHANNEL 8. Each queued reply retains its selected
channel across retries and agent restarts. The agent connects only to loopback
and never has an APRS-IS passcode.

RF access is off by default. The installer accepts it only with the commissioned
guarded Dire Wolf TX profile and physical channel 0. The agent never opens the
SA818S, audio device, or PTT GPIO itself; Dire Wolf performs normal channel
access, modulation, and PTT for channel-0 KISS frames. RF commands remain
read-only and use the same sender/global rate limits, duplicate suppression,
bounded queue, ACK matching, and retry limits as APRS-IS commands.

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
message ID. It immediately sends `ack<ID>` through the request's ingress
channel. A terminal CR/LF added by radios such as the Yaesu FT3DR is accepted as
transport padding after the message ID. The numbered response and every retry
use that same channel.

Received `(sender, message ID)` identities and a SHA-256 body digest are retained
in SQLite for 24 hours. Duplicate packets are ACKed again but their commands are
not run again. Reuse of the same sender/ID with a different body inside that
window is ACKed, logged as a conflict, and not executed. The database stores the
body only for an accepted `MSG` mailbox entry. Received command bodies outside
`MSG`, GPS coordinates, APRS-IS credentials, and radio settings are not retained.
Outbound reply bodies and delivery state are retained for bounded ACK/retry
history. The mailbox is capped and removes its oldest entries first.

Status and mailbox replies receive a persistent four-character outbound message
ID. Each reply is stored before its first transmission. The agent accepts only
an exact, unnumbered `ack<ID>` or `rej<ID>` addressed to `W8IJC-10`, and the
receipt source must match the reply recipient. An unrelated station therefore
cannot complete another station's queued message. The APRS 1.1 `{new}old`
reply-ack extension is also recognized: `old` completes the matching prior
outbound message while the newly numbered command is ACKed and processed.

Pending replies survive an agent restart. By default the first transmission is
followed after 30, 60, 120, and 240 seconds by at most four retries. A successful
socket write records an attempt; a failed KISS write leaves the message due so
it can be sent after reconnect. Exhausted messages are retained as failed, and a
late matching ACK can still complete them. ACKed, rejected, and failed history
is retained for seven days. The pending queue is capped at 100 messages.

## Commands

| Command | Reply |
| --- | --- |
| `PING` | `PONG` |
| `STATUS` or `S` | `PCS OK/BAD | Uplink - LTE/WiFi/Down | GPS 3D/NoFX | Pi Temp - XXC` |
| `POWER` | `POWER N/A` until monitoring hardware is installed |
| `LTE` | `UP`, `STANDBY`, `NO MODEM`, or `UNKNOWN` |
| `GPS` | `3D`, `2D`, `STALE`, `NO FIX`, or `UNAVAILABLE` |
| `TEMP` | Raspberry Pi thermal-zone temperature |
| `NET` | Coarse active transport: Ethernet, Wi-Fi, cellular, up, or down |
| `UPTIME` | Days/hours/minutes from `/proc/uptime` |
| `HELP` or `H` | Supported command names |
| `MSG <text>` | Store a 1-63 character mailbox message and reply `MESSAGE STORED` |

`STATUS` deliberately omits the not-yet-installed power monitor. APRS messaging
uses printable 7-bit text, so the radio reply uses `37C` rather than a degree
symbol. An unrecognized command receives `COMMAND UNKNOWN | COMMAND LIST: HELP`,
which cannot reasonably be mistaken for a distress request.

GPS replies never include coordinates. LTE and network replies never expose IP
addresses, SSIDs, carriers, IMEI/ICCID values, device names, or account data.
All subprocesses use fixed read-only argument lists with bounded timeouts; APRS
text is never passed to a shell. Per-sender and global command/reply limits
bound abuse of the public, unauthenticated APRS identity. Protocol ACKs are sent
before those limits so valid numbered duplicates are still ACKed without
re-running their commands.

## Mailbox and operator interfaces

The public homepage and public API expose only aggregate agent state: connection,
session packet/message counters, stored count, unread count, and last-message
age. They never expose a sender, message ID, or body. The authenticated admin
dashboard and authenticated APRS API resource show the ten newest retained
messages. The fixed **Mark APRS Mailbox Read** action clears the unread state
without deleting any message.

The Android companion already renders authenticated resource cards and the
fixed action catalog, so a paired app can read the mailbox under APRS details
and invoke the challenge-protected mark-read action without an app-specific
transport. A later dedicated Mailbox screen could add search, per-message read
state, and notifications. Web/app message composition remains disabled while the
outgoing engine is validated. A later operator interface can enqueue through a
narrow authenticated agent IPC endpoint and display pending, acknowledged,
rejected, and failed state; the web service must not write the agent's SQLite
database directly.

The aggregate runtime file `/run/pcs-aprs-agent/status.json` feeds the hardware
indicators. The 16x2 LCD adds `APRS Stats: Ok` or `APRS Stats: MSG` and a bounded
counter line such as `Pkt RX:0 Msgs:0`. When the system is otherwise healthy,
unread mail alternates an intensity-1 envelope and intensity-1 checkmark on the
MAX7219. WS2812 pixel 3 pulses white once per second before restoring that
pixel's normal service-health color.

## Configuration

The versioned source template is `config/aprs-agent.example.conf`. Deployment
generates `/etc/pcs/aprs-agent.conf` from the non-secret PCS install settings:

| Setting | Local default | Purpose |
| --- | --- | --- |
| `PCS_APRS_AGENT_ENABLED` | `no` | Explicitly opt in to generating the Internet channel and permitting installation. |
| `PCS_APRS_AGENT_ICHANNEL` | `8` | Unused KISS port nibble mapped to APRS-IS. Must never overlap radio channels. |
| `PCS_APRS_AGENT_RF_ENABLED` | `no` | Explicitly admit local 144.550 MHz commands through Dire Wolf's guarded TX profile. |
| `PCS_APRS_AGENT_RF_CHANNEL` | `0` | Commissioned physical Dire Wolf channel used for local requests and responses. |
| `PCS_APRS_AGENT_TOCALL` | `APZPCS` | Experimental PCS software destination identifier. |
| `PCS_APRS_AGENT_DEDUPE_TTL_SECONDS` | `86400` | Persistent duplicate window. |
| `PCS_APRS_AGENT_MAILBOX_LIMIT` | `100` | Maximum retained mailbox messages. |
| `PCS_APRS_AGENT_SENDER_RATE_PER_MINUTE` | `12` | Per-sender command/reply ceiling; required ACKs are not dropped. |
| `PCS_APRS_AGENT_GLOBAL_RATE_PER_MINUTE` | `60` | Whole-agent command/reply ceiling; required ACKs are not dropped. |
| `PCS_APRS_AGENT_OUTBOUND_RETRY_SECONDS` | `30,60,120,240` | Nondecreasing retry delays after successful transmissions. |
| `PCS_APRS_AGENT_OUTBOUND_MAX_PENDING` | `100` | Hard limit for replies awaiting ACK/REJ. |
| `PCS_APRS_AGENT_OUTBOUND_RETENTION_SECONDS` | `604800` | Retention for completed and exhausted message history. |

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
directives. RF enablement additionally requires the guarded commissioned TX
profile and channel 0. The installer does not edit or restart Dire Wolf, collect
credentials, or directly touch GPIO/PTT/audio. Once RF access is enabled, an
addressed radio command can cause Dire Wolf to transmit the protocol ACK and
numbered response on 144.550 MHz.
