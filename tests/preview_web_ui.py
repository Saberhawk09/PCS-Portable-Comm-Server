"""Loopback fixture preview. No PCS probes, sudo calls or privileged actions.

Run: python tests/preview_web_ui.py [--port 8766]
Login: ui-preview-password (fixture only). POST actions return labelled fixture
output. Never use this preview server or fixture credentials on an appliance.
"""
import argparse
from copy import deepcopy
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent))
from test_pcs_control_panel import pcs, PUBLIC_DATA, ADMIN_DATA, ROOT

public = deepcopy(PUBLIC_DATA)
public['generated_at'] = 'LOCAL FIXTURE — no appliance connected'
public['network']['ap_client_count'] = 0
public['power'] = {'configured': True, 'input_online': True, 'input_power': 0,
                   'input_voltage': 12.6, 'dc_source': 'battery', 'shutdown_armed': True}
admin = deepcopy(ADMIN_DATA)
admin.update(generated_at=public['generated_at'], snapshot={'state': 'fresh', 'age_seconds': 0})
admin['cards'] = [
    {'id': key, 'title': title, 'status': 'ok', 'summary': summary,
     'items': [{'label': 'Fixture detail', 'value': 'Local preview only'}]}
    for key, title, summary in [
        ('network', 'Network', 'LAN available · Wi-Fi uplink'),
        ('client-lan', 'Client LAN', 'AP clients: 0'), ('gps', 'GNSS', '3D fix · 8 satellites'),
        ('power', 'PCS Power', 'Battery · input 12.6 V · 0 W'),
        ('storage', 'Storage', 'Primary share available'), ('backup-health', 'Backup health', 'Automatic backups disabled'),
        ('aprs', 'APRS', 'Not configured'), ('meshtastic', 'Meshtastic', 'Not configured'),
        ('starlink', 'Starlink Telemetry', 'Not commissioned'),
        ('services', 'Services', 'Fixture services'), ('future-card', 'Future diagnostics', 'Preserved fallback card')]]

# Mock every appliance-facing boundary before starting the actual auth handlers.
pcs.get_public_dashboard = lambda: pcs.sanitize_public_dashboard(public)
pcs.get_admin_dashboard = lambda: deepcopy(admin)
pcs.load_backup_settings = lambda: ({'enabled': False, 'interval_minutes': 10, 'keep_history': False}, '')
pcs.run_action = lambda action: (0, 'LOCAL FIXTURE ONLY: received ' + action + '\nNo hardware action executed.')
pcs.update_backup_settings = lambda *args: (True, 'LOCAL FIXTURE ONLY: settings not saved.')
pcs.bandwidth_request = lambda *args: {'status': 'LOCAL FIXTURE — unavailable', 'config': {}}
pcs.location_request = lambda *args: {'config': {'enabled': True, 'persist': False, 'latitude': 0, 'longitude': 0}, 'status': {'source': 'Manual'}}
pcs.change_admin_password = lambda *args: (False, 'Password changes disabled in fixture preview.')
pcs.AUTH.configured = lambda: True
pcs.AUTH.verify = lambda password: password == 'ui-preview-password'
pcs.SESSIONS = pcs.SessionStore(b'fixture-only-key-not-for-production')


class Handler(pcs.Handler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path in {'/', '/power/', '/starlink/', '/docs/', '/files/', '/radio/', '/pistar/'} or path.startswith('/assets/'):
            relative = path.removeprefix('/assets/') if path.startswith('/assets/') else path.lstrip('/') + 'index.html'
            target = (ROOT / 'web/pcs-home' / relative).resolve()
            if not target.is_relative_to((ROOT / 'web/pcs-home').resolve()) or not target.is_file():
                self.send_error(404)
                return
            mime = 'text/css' if target.suffix == '.css' else 'application/javascript' if target.suffix == '.js' else 'text/html; charset=utf-8'
            # Match nginx's static self-only policy; backend documents keep their nonce policy.
            content = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(content)
            return
        return super().do_GET()

    def do_POST(self):
        # Power uses subprocess directly in the real handler; block that route here.
        if urlsplit(self.path).path == '/admin/power-settings':
            self.send_body(400, b'LOCAL FIXTURE: power settings are disabled.', 'text/plain')
            return
        return super().do_POST()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    print(f'LOCAL FIXTURE ONLY http://127.0.0.1:{args.port}/', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
