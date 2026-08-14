#!/usr/bin/env python3

import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("PCS_REDIRECT_HOST", "0.0.0.0")
PORT = int(os.environ.get("PCS_REDIRECT_PORT", "8080"))
TARGET_PORT = os.environ.get("PCS_CONTROL_PORT", "80")
TARGET_PATH = os.environ.get("PCS_CONTROL_PATH", "/admin/")


class RedirectHandler(BaseHTTPRequestHandler):
    def target(self):
        requested_host = self.headers.get("Host", "10.42.0.1").split(":", 1)[0]
        host = requested_host if re.fullmatch(r"[A-Za-z0-9.-]+", requested_host) else "10.42.0.1"
        port = "" if TARGET_PORT == "80" else f":{TARGET_PORT}"
        return f"http://{host}{port}{TARGET_PATH}"

    def common_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.common_headers()
            self.end_headers()
            self.wfile.write(b"PCS legacy admin redirect healthy\n")
            return

        target = self.target()

        self.send_response(308)
        self.send_header("Location", target)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.common_headers()
        self.end_headers()

        body = f"""<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>PCS Administration Redirect</title>
    <meta http-equiv="refresh" content="0; url={target}">
</head>
<body>
    <p>Redirecting to <a href="{target}">{target}</a></p>
</body>
</html>
"""
        self.wfile.write(body.encode("utf-8"))

    def do_HEAD(self):
        target = self.target()

        self.send_response(308)
        self.send_header("Location", target)
        self.common_headers()
        self.end_headers()

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} - {fmt % args}")


def main():
    server = ThreadingHTTPServer((HOST, PORT), RedirectHandler)
    print(f"PCS legacy admin redirect listening on http://{HOST}:{PORT}")
    print(f"Redirect target: port {TARGET_PORT}{TARGET_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
