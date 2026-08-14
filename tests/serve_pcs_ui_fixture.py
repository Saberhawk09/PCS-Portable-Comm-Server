#!/usr/bin/env python3

"""Serve representative PCS UI data for local browser layout checks."""

import importlib.util
import tempfile
from copy import deepcopy
from pathlib import Path

from test_pcs_control_panel import ADMIN_DATA, PUBLIC_DATA


MODULE_PATH = Path(__file__).parents[1] / "web" / "pcs-control-panel" / "pcs_control_panel.py"
SPEC = importlib.util.spec_from_file_location("pcs_control_panel_preview", MODULE_PATH)
pcs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pcs)

temporary = tempfile.TemporaryDirectory()
credential = str(Path(temporary.name) / "admin.json")
pcs.write_password_record(credential, "correct horse battery staple")
pcs.AUTH = pcs.AuthManager(credential)
pcs.SESSIONS = pcs.SessionStore(b"preview-key" * 4, ttl=3600)
pcs.get_public_dashboard = lambda: pcs.sanitize_public_dashboard(PUBLIC_DATA)

admin_preview = deepcopy(ADMIN_DATA)
base_card = admin_preview["cards"][0]
admin_preview["cards"] = [
    {**deepcopy(base_card), "id": title.lower().replace(" ", "-"), "title": title}
    for title in ("System", "Network", "Cellular", "GNSS", "Storage", "Time", "Services", "Pi-Star")
]
pcs.get_admin_dashboard = lambda: admin_preview
pcs.run_action = lambda action: (0, f"Preview action completed: {action}\n")

server = pcs.ReusableThreadingHTTPServer(("127.0.0.1", 8765), pcs.Handler)
print("PCS UI fixture: http://127.0.0.1:8765/", flush=True)
print("Preview admin password: correct horse battery staple", flush=True)

try:
    server.serve_forever()
finally:
    server.server_close()
    temporary.cleanup()
