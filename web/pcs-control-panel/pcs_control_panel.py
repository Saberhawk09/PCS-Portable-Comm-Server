#!/usr/bin/env python3

import html
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
    ("sync-backup", "Sync USB → SD Backup", "Mirror USB primary share to SD backup."),
    ("mount-usb", "Mount USB Share", "Mount USB primary storage and restart Samba."),
    ("safe-unmount-usb", "Safely Unmount USB", "Sync backup, stop Samba, unmount USB, restart Samba."),
    ("restart-services", "Restart PCS Services", "Restart core PCS services through systemd."),
    ("restart-samba", "Restart Samba", "Restart Samba only."),
    ("restart-chrony", "Restart Chrony", "Restart Chrony only."),
    ("restart-logs", "Restart Logs", "Show recent PCS restart service logs."),
]

ACTION_MAP = {name: (label, desc) for name, label, desc in ACTIONS}


def run_action(action: str) -> tuple[int, str]:
    if action not in ACTION_MAP:
        return 2, f"Unknown action: {action}\n"

    try:
        result = subprocess.run(
            ["sudo", "-n", DISPATCHER, action],
            text=True,
            capture_output=True,
            timeout=300,
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


def page(action_result: str = "", action_name: str = "", return_code: int | None = None) -> bytes:
    buttons = []

    for name, label, desc in ACTIONS:
        danger = name in {"safe-unmount-usb", "restart-services", "restart-samba", "restart-chrony"}
        css_class = "danger" if danger else "normal"

        buttons.append(
            f"""
            <form method="POST" action="/run" class="action-card">
                <input type="hidden" name="action" value="{html.escape(name)}">
                <button class="{css_class}" type="submit">{html.escape(label)}</button>
                <p>{html.escape(desc)}</p>
            </form>
            """
        )

    if action_result:
        result_block = f"""
        <section class="output">
            <h2>Result: {html.escape(action_name)} <span class="code">exit {return_code}</span></h2>
            <pre>{html.escape(action_result)}</pre>
        </section>
        """
    else:
        result_block = """
        <section class="output muted">
            <h2>No action run yet</h2>
            <p>Choose a button above. Output will appear here.</p>
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
            --bg: #101216;
            --panel: #181b21;
            --panel2: #20242c;
            --text: #e8eaf0;
            --muted: #a9b0bd;
            --accent: #78a6ff;
            --danger: #ff7b7b;
            --ok: #9ee493;
            --border: #303642;
        }}

        body {{
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: var(--bg);
            color: var(--text);
        }}

        header {{
            padding: 1.25rem;
            border-bottom: 1px solid var(--border);
            background: var(--panel);
        }}

        header h1 {{
            margin: 0;
            font-size: 1.5rem;
        }}

        header p {{
            margin: 0.35rem 0 0;
            color: var(--muted);
        }}

        main {{
            padding: 1.25rem;
            max-width: 1200px;
            margin: 0 auto;
        }}

        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1rem;
        }}

        .action-card {{
            background: var(--panel);
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
            font-weight: 700;
            cursor: pointer;
        }}

        button.normal {{
            background: var(--accent);
            color: #08101f;
        }}

        button.danger {{
            background: var(--danger);
            color: #240707;
        }}

        .action-card p {{
            color: var(--muted);
            margin: 0.75rem 0 0;
            line-height: 1.35;
        }}

        .output {{
            margin-top: 1.25rem;
            background: var(--panel2);
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
            background: #0b0d11;
            padding: 1rem;
            border-radius: 10px;
            overflow-x: auto;
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
        <section class="grid">
            {''.join(buttons)}
        </section>

        {result_block}
    </main>

    <footer>
        PCS local panel. Do not expose this interface to the public internet.
    </footer>
</body>
</html>
"""
    return body.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
