#!/usr/bin/env python3
"""Serve the frontend production build and proxy /api to the backend."""
import http.server
import http.client
import os
import socketserver
from urllib.parse import urljoin

DIST_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
PORT = 5174


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIST_DIR, **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._proxy("GET")
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy("POST")
        else:
            self.send_error(405)

    def do_PATCH(self):
        if self.path.startswith("/api/"):
            self._proxy("PATCH")
        else:
            self.send_error(405)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy("DELETE")
        else:
            self.send_error(405)

    def _proxy(self, method):
        # Read request body if present
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else None

        # Forward headers
        headers = {}
        for key in self.headers:
            if key.lower() not in ("host", "content-length", "connection"):
                headers[key] = self.headers[key]

        # Connect to backend
        conn = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=600)
        try:
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            resp_body = resp.read()
        finally:
            conn.close()

        self.send_response(resp.status)
        for key, value in resp.getheaders():
            if key.lower() not in ("connection", "transfer-encoding", "content-encoding"):
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)


class QuietHandler(ProxyHandler):
    def log_message(self, format: str, *args) -> None:
        pass


if __name__ == "__main__":
    os.chdir(DIST_DIR)
    with socketserver.TCPServer(("0.0.0.0", PORT), QuietHandler) as httpd:
        print(f"Serving frontend from {DIST_DIR} on port {PORT}, proxying /api to {BACKEND_HOST}:{BACKEND_PORT}")
        httpd.serve_forever()
