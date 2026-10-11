#!/usr/bin/env python3
"""Local OpenAI-compatible shim in front of OpenCode Go (QA environment only).

OpenJM's model gateway sends only ``Content-Type`` and ``Authorization``. OpenCode
Go additionally requires a client-specific User-Agent and a stable
``x-opencode-session`` header (see https://opencode.ai/docs/go/). Rather than
change the accepted model-provider abstraction, this local endpoint presents the
OpenAI-compatible surface OpenJM already supports and injects those two headers
on the way out.

* Listens on 127.0.0.1 only.
* Reads the credential from the environment; it is never logged or echoed.
* Forwards only ``POST /v1/chat/completions`` and ``GET /v1/models``.
* Uses one stable session id per process so provider-side prompt caching applies.

Run:  OPENCODE_GO_API_KEY=... python3 opencode_go_shim.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://opencode.ai/zen/go/v1"
PORT = int(os.environ.get("OPENJM_QA_SHIM_PORT", "18081"))
SESSION_ID = os.environ.get("OPENJM_QA_SHIM_SESSION", str(uuid.uuid4()))
USER_AGENT = "openjm-enterprise-ai-qa/0.2.0"


def _api_key() -> str:
    key = os.environ.get("OPENCODE_GO_API_KEY", "").strip()
    if not key:
        sys.stderr.write("OPENCODE_GO_API_KEY is not set\n")
        sys.exit(2)
    return key


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # never log bodies or headers
        sys.stderr.write("shim: " + (fmt % args if args else fmt) + "\n")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self, method: str, path: str, body: bytes | None) -> None:
        req = urllib.request.Request(
            UPSTREAM + path,
            data=body,
            method=method,
            headers={
                "Authorization": "Bearer " + _api_key(),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "x-opencode-session": SESSION_ID,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                payload = resp.read()
                self._send(resp.status, payload, resp.headers.get("Content-Type", "application/json"))
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self._send(exc.code, payload, exc.headers.get("Content-Type", "application/json"))
        except Exception as exc:  # noqa: BLE001
            detail = json.dumps({"error": {"message": f"shim upstream failure: {type(exc).__name__}"}})
            self._send(502, detail.encode(), "application/json")

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") == "/v1/models":
            self._forward("GET", "/models", None)
        else:
            self._send(404, b'{"error":{"message":"not found"}}', "application/json")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.path.rstrip("/") == "/v1/chat/completions":
            self._forward("POST", "/chat/completions", body)
        else:
            self._send(404, b'{"error":{"message":"not found"}}', "application/json")


if __name__ == "__main__":
    _api_key()  # fail fast if unset
    sys.stderr.write(f"opencode-go shim on 127.0.0.1:{PORT} (session {SESSION_ID[:8]}...)\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
