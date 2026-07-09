#!/usr/bin/env python3

import html
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

HOST = os.environ.get("PCS_CONTROL_HOST", "0.0.0.0")
PORT = int(os.environ.get("PCS_CONTROL_PORT", "8080"))

DISPATCHER = "/usr/local/sbin/pcs-web-action"

ACTIONS = [
    ("status", "PCS Status", "Show full PCS status output."),
    ("self-test", "PCS Self-Test", "Run the Pi-side validation test."),
    ("storage-status", "Storage Status", "Show USB/SD/Samba storage state."),
    ("wifi-status", "Wi-Fi Status", "Show Wi-Fi uplink status and default route."),
    ("wifi-connect", "Connect Wi-Fi Uplink", "Scan for saved Wi-Fi networks and connect to the strongest visible match."),
    ("wifi-disconnect", "Disable Wi-Fi Radio", "Turn off the Pi Wi-Fi radio so PCS can run offline or on cellular."),
    ("cellular-status", "Cellular Status", "Show WWAN modem and cellular connection state."),
    ("cellular-connect", "Connect Cellular", "Bring up the manual cellular data connection."),
    ("cellular-disconnect", "Disconnect Cellular", "Bring down the manual cellular data connection."),
    ("cellular-test", "Test Cellular Internet", "Test cellular-only internet access through wwan0."),
    ("sync-backup", "Sync USB → SD Backup", "Mirror USB primary share to SD backup."),
    ("mount-usb", "Mount USB Share", "Mount USB primary storage and restart Samba."),
    ("mount-new-usb", "Mount New USB Share", "Configure the newly attached USB device as the PCS primary share."),
    ("safe-unmount-usb", "Safely Unmount USB", "Sync backup, stop Samba, unmount USB, restart Samba."),
    ("restart-services", "Restart PCS Services", "Restart core PCS services through systemd."),
    ("restart-samba", "Restart Samba", "Restart Samba only."),
    ("sync-time", "Sync Time Now", "Poll Chrony sources, step the system clock if needed, and update the RTC."),
    ("restart-chrony", "Restart Chrony", "Restart Chrony only."),
    ("restart-gpsd", "Restart GPSD", "Reassert WWAN NMEA mode and restart gpsd."),
    ("restart-logs", "Restart Logs", "Show recent PCS restart service logs."),
]

ACTION_MAP = {name: (label, desc) for name, label, desc in ACTIONS}

ACTION_GROUPS = [
    ("Status", ["status", "self-test", "storage-status", "restart-logs"]),
    ("Network", ["wifi-status", "wifi-connect", "wifi-disconnect"]),
    ("Cellular", ["cellular-status", "cellular-connect", "cellular-disconnect", "cellular-test"]),
    ("Storage", ["sync-backup", "mount-usb", "mount-new-usb", "safe-unmount-usb"]),
    ("Services", ["restart-services", "restart-samba"]),
    ("Time / GPS", ["sync-time", "restart-chrony", "restart-gpsd"]),
]


def run_dispatcher(action: str, timeout: int = 300) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["sudo", "-n", DISPATCHER, action],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\n--- STDERR ---\n" + result.stderr
        return result.returncode, output
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        output += "\nERROR: action timed out.\n"
        return 124, output
    except Exception as exc:
        return 1, f"ERROR: {exc}\n"


def get_dashboard() -> dict:
    code, output = run_dispatcher("dashboard-json", timeout=30)

    if code != 0:
        return {
            "generated_at": "unknown",
            "overall": "bad",
            "cards": [
                {
                    "id": "dashboard",
                    "title": "Dashboard",
                    "status": "bad",
                    "summary": "Dashboard data failed to load",
                    "items": [
                        {"label": "Exit code", "value": str(code)},
                        {"label": "Output", "value": output[-500:] or "no output"},
                    ],
                }
            ],
        }

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {
            "generated_at": "unknown",
            "overall": "bad",
            "cards": [
                {
                    "id": "dashboard",
                    "title": "Dashboard",
                    "status": "bad",
                    "summary": "Dashboard JSON was invalid",
                    "items": [
                        {"label": "Output", "value": output[-500:] or "no output"},
                    ],
                }
            ],
        }


def run_action(action: str) -> tuple[int, str]:
    if action not in ACTION_MAP:
        return 2, f"Unknown action: {action}\n"

    return run_dispatcher(action)


def esc(value) -> str:
    return html.escape(str(value))


def status_label(status: str) -> str:
    return {
        "ok": "OK",
        "warn": "WARN",
        "bad": "BAD",
    }.get(status, status.upper())


def render_metric(metric: dict) -> str:
    label = esc(metric.get("label", "Metric"))
    value = metric.get("value")
    suffix = esc(metric.get("suffix", ""))

    if value is None:
        percent = 0
        text = "unknown"
    else:
        try:
            percent = max(0, min(100, float(value)))
            text = f"{percent:g}{suffix}"
        except Exception:
            percent = 0
            text = esc(value)

    return f"""
    <div class="metric">
        <div class="metric-row">
            <span>{label}</span>
            <span>{text}</span>
        </div>
        <div class="bar">
            <div class="bar-fill" style="width: {percent}%;"></div>
        </div>
    </div>
    """


def render_card(card: dict) -> str:
    status = esc(card.get("status", "warn"))
    title = esc(card.get("title", "Card"))
    summary = esc(card.get("summary", ""))

    items_html = []
    for item in card.get("items", []):
        items_html.append(
            f"""
            <div class="item">
                <span class="item-label">{esc(item.get("label", ""))}</span>
                <span class="item-value">{esc(item.get("value", ""))}</span>
            </div>
            """
        )

    metrics_html = []
    for metric in card.get("metrics", []):
        metrics_html.append(render_metric(metric))

    return f"""
    <article class="dash-card {status}">
        <div class="card-top">
            <h2>{title}</h2>
            <span class="badge {status}">{status_label(status)}</span>
        </div>
        <p class="summary">{summary}</p>
        <div class="metrics">
            {''.join(metrics_html)}
        </div>
        <div class="items">
            {''.join(items_html)}
        </div>
    </article>
    """


def render_dashboard(dashboard: dict) -> str:
    overall = esc(dashboard.get("overall", "warn"))
    generated = esc(dashboard.get("generated_at", "unknown"))

    cards = "".join(render_card(card) for card in dashboard.get("cards", []))

    return f"""
    <section class="overview {overall}">
        <div>
            <h2>System overview</h2>
            <p>Generated at {generated}</p>
        </div>
        <span class="overall-badge {overall}">{status_label(overall)}</span>
    </section>

    <section class="dashboard-grid">
        {cards}
    </section>
    """



def render_client_info(dashboard: dict) -> str:
    info = dashboard.get("client_info", {})

    router_ip = esc(info.get("router_ip", "10.42.0.1"))
    wan_public_ip = esc(info.get("wan_public_ip", "unavailable"))
    uplink_interface = esc(info.get("uplink_interface", "unknown"))
    uplink_source_ip = esc(info.get("uplink_source_ip", "unknown"))
    clients = info.get("router_side_clients", [])

    if clients:
        client_rows = []
        for client in clients:
            name = esc(client.get("name", "unknown"))
            ip = esc(client.get("ip", "unknown"))
            mac = esc(client.get("mac", "unknown"))
            state = esc(client.get("state", "unknown"))
            client_rows.append(
                f"""
                <div class="copy-line">
                    <span>{name}</span>
                    <code>{ip} · {mac} · {state}</code>
                </div>
                """
            )
        clients_html = "".join(client_rows)
    else:
        clients_html = """
        <div class="copy-line">
            <span>No router-side clients visible</span>
            <code>None seen on eth0</code>
        </div>
        """

    return f"""
    <section class="client-info">
        <div class="client-info-top">
            <div>
                <h2>Local client info</h2>
                <p>Use these addresses from a laptop or phone connected behind the PCS/test router.</p>
            </div>
            <span class="client-pill">{router_ip}</span>
        </div>

        <div class="client-grid">
            <div class="client-card">
                <h3>File shares</h3>
                <div class="copy-line"><span>Primary USB share</span><code>\\\\10.42.0.1\\PCS-Share</code></div>
                <div class="copy-line"><span>SD backup share</span><code>\\\\10.42.0.1\\PCS-Backup</code></div>
            </div>

            <div class="client-card">
                <h3>Web interfaces</h3>
                <div class="copy-line"><span>PCS Control Panel</span><code>http://10.42.0.1:8080</code></div>
                <div class="copy-line"><span>Cockpit</span><code>https://10.42.0.1:9090</code></div>
            </div>

            <div class="client-card">
                <h3>WAN / uplink</h3>
                <div class="copy-line"><span>WAN/public IP</span><code>{wan_public_ip}</code></div>
                <div class="copy-line"><span>Uplink interface</span><code>{uplink_interface}</code></div>
                <div class="copy-line"><span>Uplink source IP</span><code>{uplink_source_ip}</code></div>
            </div>

            <div class="client-card">
                <h3>Time service</h3>
                <div class="copy-line"><span>LAN NTP server</span><code>10.42.0.1</code></div>
                <div class="copy-line"><span>Windows NTP test</span><code>w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly</code></div>
            </div>

            <div class="client-card">
                <h3>Quick Windows tests</h3>
                <div class="copy-line"><span>Internet</span><code>ping 8.8.8.8</code></div>
                <div class="copy-line"><span>DNS</span><code>ping google.com</code></div>
                <div class="copy-line"><span>Pi access</span><code>ping 10.42.0.1</code></div>
            </div>

            <div class="client-card">
                <h3>Router-side clients seen by Pi</h3>
                {clients_html}
                <p class="client-note">Note: clients behind the router NAT may only appear as the router WAN device.</p>
            </div>
        </div>
    </section>
    """




def render_action_card(name: str) -> str:
    label, desc = ACTION_MAP[name]
    danger = name in {"mount-new-usb", "safe-unmount-usb", "restart-services", "restart-samba", "restart-chrony", "restart-gpsd"}
    css_class = "danger" if danger else "normal"

    return f"""
    <form method="POST" action="/run" class="action-card">
        <input type="hidden" name="action" value="{esc(name)}">
        <button class="{css_class}" type="submit">{esc(label)}</button>
        <p>{esc(desc)}</p>
    </form>
    """


def render_action_groups() -> str:
    groups = []

    for title, action_names in ACTION_GROUPS:
        cards = "".join(render_action_card(name) for name in action_names if name in ACTION_MAP)

        groups.append(f"""
        <section class="action-group">
            <h3>{esc(title)}</h3>
            <div class="grid">
                {cards}
            </div>
        </section>
        """)

    return "".join(groups)



def page(action_result: str = "", action_name: str = "", return_code: int | None = None) -> bytes:
    dashboard = get_dashboard()

    action_sections = render_action_groups()

    if action_result:
        result_block = f"""
        <section class="output">
            <h2>Result: {esc(action_name)} <span class="code">exit {return_code}</span></h2>
            <pre>{esc(action_result)}</pre>
        </section>
        """
    else:
        result_block = """
        <section class="output muted">
            <h2>No action run yet</h2>
            <p>Use the dashboard for a quick health check, or choose a command button for detailed output.</p>
        </section>
        """

    body = f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>PCS Control Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {{
            color-scheme: dark;
            --bg: #0e1117;
            --panel: #171b22;
            --panel2: #1f2530;
            --panel3: #11151c;
            --text: #e9edf5;
            --muted: #9fa8b8;
            --border: #303846;
            --ok: #8de38d;
            --ok-bg: rgba(141, 227, 141, 0.13);
            --warn: #ffd166;
            --warn-bg: rgba(255, 209, 102, 0.14);
            --bad: #ff7b7b;
            --bad-bg: rgba(255, 123, 123, 0.14);
            --accent: #78a6ff;
            --button-text: #07111f;
        }}

        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background:
                radial-gradient(circle at top left, rgba(120, 166, 255, 0.16), transparent 28rem),
                radial-gradient(circle at bottom right, rgba(141, 227, 141, 0.08), transparent 24rem),
                var(--bg);
            color: var(--text);
        }}

        header {{
            padding: 1.25rem;
            border-bottom: 1px solid var(--border);
            background: rgba(23, 27, 34, 0.92);
            position: sticky;
            top: 0;
            backdrop-filter: blur(12px);
            z-index: 10;
        }}

        header h1 {{
            margin: 0;
            font-size: 1.65rem;
            letter-spacing: 0.02em;
        }}

        header p {{
            margin: 0.35rem 0 0;
            color: var(--muted);
        }}

        main {{
            padding: 1.25rem;
            max-width: 1850px;
            margin: 0 auto;
        }}

        .overview {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 1rem 1.15rem;
            margin-bottom: 1rem;
        }}

        .overview h2 {{
            margin: 0;
        }}

        .overview p {{
            margin: 0.25rem 0 0;
            color: var(--muted);
        }}

        .overall-badge,
        .badge {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            font-weight: 800;
            letter-spacing: 0.04em;
        }}

        .overall-badge {{
            min-width: 5rem;
            padding: 0.55rem 0.8rem;
        }}

        .badge {{
            min-width: 3.7rem;
            padding: 0.35rem 0.55rem;
            font-size: 0.75rem;
        }}

        .ok {{
            border-color: rgba(141, 227, 141, 0.45);
        }}

        .warn {{
            border-color: rgba(255, 209, 102, 0.5);
        }}

        .bad {{
            border-color: rgba(255, 123, 123, 0.55);
        }}

        .badge.ok,
        .overall-badge.ok {{
            background: var(--ok-bg);
            color: var(--ok);
        }}

        .badge.warn,
        .overall-badge.warn {{
            background: var(--warn-bg);
            color: var(--warn);
        }}

        .badge.bad,
        .overall-badge.bad {{
            background: var(--bad-bg);
            color: var(--bad);
        }}

        .dashboard-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 0.85rem;
            margin-bottom: 1.25rem;
        }}

        .dash-card {{
            background: rgba(23, 27, 34, 0.92);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 0.9rem;
            box-shadow: 0 12px 30px rgba(0, 0, 0, 0.18);
        }}

        .card-top {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.75rem;
        }}

        .card-top h2 {{
            margin: 0;
            font-size: 1.05rem;
        }}

        .summary {{
            margin: 0.6rem 0 0.85rem;
            color: var(--muted);
        }}

        .items {{
            display: grid;
            gap: 0.45rem;
        }}

        .item {{
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            border-top: 1px solid rgba(255,255,255,0.06);
            padding-top: 0.45rem;
        }}

        .item-label {{
            color: var(--muted);
        }}

        .item-value {{
            text-align: right;
            overflow-wrap: anywhere;
        }}

        .metrics {{
            display: grid;
            gap: 0.7rem;
            margin-bottom: 0.85rem;
        }}

        .metric-row {{
            display: flex;
            justify-content: space-between;
            color: var(--muted);
            font-size: 0.9rem;
            margin-bottom: 0.25rem;
        }}

        .bar {{
            height: 0.65rem;
            background: var(--panel3);
            border: 1px solid var(--border);
            border-radius: 999px;
            overflow: hidden;
        }}

        .bar-fill {{
            height: 100%;
            background: linear-gradient(90deg, var(--accent), var(--ok));
            border-radius: inherit;
        }}

        .client-info {{
            background: rgba(23, 27, 34, 0.92);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 1rem;
            margin-bottom: 1.25rem;
        }}

        .client-info-top {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            margin-bottom: 1rem;
        }}

        .client-info h2,
        .client-info h3 {{
            margin: 0;
        }}

        .client-info p {{
            margin: 0.35rem 0 0;
            color: var(--muted);
        }}

        .client-pill {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            padding: 0.55rem 0.85rem;
            background: rgba(120, 166, 255, 0.15);
            color: var(--accent);
            font-weight: 800;
            white-space: nowrap;
        }}

        .client-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(245px, 1fr));
            gap: 0.85rem;
        }}

        .client-card {{
            background: rgba(17, 21, 28, 0.75);
            border: 1px solid rgba(255,255,255,0.07);
            border-radius: 14px;
            padding: 0.9rem;
        }}

        .client-card h3 {{
            font-size: 1rem;
            margin-bottom: 0.7rem;
        }}

        .copy-line {{
            display: grid;
            gap: 0.25rem;
            padding-top: 0.55rem;
            margin-top: 0.55rem;
            border-top: 1px solid rgba(255,255,255,0.06);
        }}

        .copy-line span {{
            color: var(--muted);
            font-size: 0.9rem;
        }}

        .copy-line code {{
            display: block;
            background: #090c11;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 0.45rem 0.55rem;
            overflow-wrap: anywhere;
        }}

        .client-note {{
            color: var(--muted);
            font-size: 0.85rem;
            margin: 0.75rem 0 0;
            line-height: 1.35;
        }}

        .actions-title {{
            margin: 1.3rem 0 0.75rem;
        }}

        .action-group {{
            margin-bottom: 1.25rem;
        }}

        .action-group h3 {{
            margin: 0 0 0.6rem;
            color: var(--muted);
            font-size: 1rem;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }}

        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 0.85rem;
        }}

        .action-card {{
            background: rgba(23, 27, 34, 0.9);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 1rem;
        }}

        button {{
            width: 100%;
            border: 0;
            border-radius: 10px;
            padding: 0.75rem 1rem;
            font-size: 1rem;
            font-weight: 800;
            cursor: pointer;
        }}

        button.normal {{
            background: var(--accent);
            color: var(--button-text);
        }}

        button.danger {{
            background: var(--bad);
            color: #240707;
        }}

        .action-card p {{
            color: var(--muted);
            margin: 0.75rem 0 0;
            line-height: 1.35;
        }}

        .output {{
            margin-top: 1.25rem;
            background: rgba(31, 37, 48, 0.92);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 1rem;
        }}

        .output h2 {{
            margin-top: 0;
        }}

        .muted {{
            color: var(--muted);
        }}

        .code {{
            font-size: 0.85rem;
            color: var(--muted);
            font-weight: 400;
        }}

        pre {{
            white-space: pre-wrap;
            word-break: break-word;
            background: #090c11;
            padding: 1rem;
            border-radius: 10px;
            overflow-x: auto;
            max-height: 45vh;
        }}

        @media (min-width: 1500px) {{
            .dashboard-grid {{
                grid-template-columns: repeat(6, minmax(0, 1fr));
            }}

            .client-grid {{
                grid-template-columns: repeat(6, minmax(0, 1fr));
            }}

            .grid {{
                grid-template-columns: repeat(4, minmax(0, 1fr));
            }}
        }}

        footer {{
            padding: 1rem 1.25rem 2rem;
            color: var(--muted);
            text-align: center;
        }}
    </style>
</head>
<body>
    <header>
        <h1>PCS Control Panel</h1>
        <p>Portable Communication Server local operator panel</p>
    </header>

    <main>
        {render_dashboard(dashboard)}

        {render_client_info(dashboard)}

        <h2 class="actions-title">Actions</h2>
        {action_sections}

        {result_block}
    </main>

    <footer>
        PCS local panel. Do not expose this interface to the public internet.
    </footer>
</body>
</html>
"""
    return body.encode("utf-8")


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(page())

    def do_POST(self):
        if self.path != "/run":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        fields = parse_qs(raw)
        action = fields.get("action", [""])[0]

        code, output = run_action(action)
        label = ACTION_MAP.get(action, (action, ""))[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(page(output, label, code))

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} - {fmt % args}")


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"PCS Control Panel listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
