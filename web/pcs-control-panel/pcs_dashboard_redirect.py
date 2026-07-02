#!/usr/bin/env python3

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("PCS_REDIRECT_HOST", "0.0.0.0")
PORT = int(os.environ.get("PCS_REDIRECT_PORT", "80"))
TARGET_PORT = os.environ.get("PCS_CONTROL_PORT", "8080")


class RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"PCS dashboard redirect healthy\n")
            return

        host = self.headers.get("Host", "10.42.0.1")
        host_without_port = host.split(":")[0]

        target = f"http://{host_without_port}:{TARGET_PORT}/"

        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        body = f"""<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>PCS Dashboard Redirect</title>
    <meta http-equiv="refresh" content="0; url={target}">
</head>
<body>
    <p>Redirecting to <a href="{target}">{target}</a></p>
</body>
</html>
"""
        self.wfile.write(body.encode("utf-8"))

    def do_HEAD(self):
        host = self.headers.get("Host", "10.42.0.1")
        host_without_port = host.split(":")[0]
        target = f"http://{host_without_port}:{TARGET_PORT}/"

        self.send_response(302)
        self.send_header("Location", target)
        self.end_headers()

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} - {fmt % args}")


def main():
    server = ThreadingHTTPServer((HOST, PORT), RedirectHandler)
    print(f"PCS dashboard redirect listening on http://{HOST}:{PORT}")
    print(f"Redirect target port: {TARGET_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
