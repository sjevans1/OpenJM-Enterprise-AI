"""Shared helpers for the QA1 production-like acceptance environment.

Used by scripts/qa/seed_acceptance_env.py and scripts/qa/smoke_matrix.py.

Everything here is real: a real OIDC provider (Keycloak) authorization-code +
PKCE flow against a real OpenJM backend. No dev-auth, no forged headers.

Requires the REL1 production virtualenv (httpx is a runtime dependency there).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

import httpx

KEYCLOAK_BASE = os.environ.get("OPENJM_QA_KEYCLOAK", "http://127.0.0.1:18090")
REALM = os.environ.get("OPENJM_QA_REALM", "openjm")
BACKEND = os.environ.get("OPENJM_QA_BACKEND", "http://127.0.0.1:18010")
FRONTEND = os.environ.get("OPENJM_QA_FRONTEND", "http://127.0.0.1:15173")
REDIRECT_URI = os.environ.get("OPENJM_QA_REDIRECT", f"{FRONTEND}/auth/callback")
CLIENT_ID = os.environ.get("OPENJM_QA_CLIENT_ID", "openjm")

PERSONAS = ("qa.employee", "qa.steward", "qa.clientadmin", "qa.other", "qa.platform")


def env_root() -> Path:
    return Path(os.environ.get("OPENJM_QA_ENV_ROOT", str(Path.home() / "openjm-qa1-env")))


def secrets_dir() -> Path:
    return env_root() / "secrets"


def artifacts_dir() -> Path:
    d = env_root() / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_secret(name: str) -> str:
    return (secrets_dir() / name).read_text().strip()


def write_secret(name: str, value: str) -> None:
    path = secrets_dir() / name
    path.write_text(value)
    path.chmod(0o600)


# --------------------------------------------------------------------------
# Keycloak admin
# --------------------------------------------------------------------------


class KeycloakAdmin:
    def __init__(self) -> None:
        self.base = KEYCLOAK_BASE
        self.realm = REALM
        env_file = secrets_dir() / "keycloak.env"
        values = {}
        for line in env_file.read_text().splitlines():
            if "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
        self.admin_user = values.get("KC_BOOTSTRAP_ADMIN_USERNAME", "admin")
        self.admin_password = values.get("KC_BOOTSTRAP_ADMIN_PASSWORD", "")
        self._token: str | None = None
        self._token_at = 0.0

    def token(self) -> str:
        if self._token and time.monotonic() - self._token_at < 60:
            return self._token
        response = httpx.post(
            f"{self.base}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.admin_user,
                "password": self.admin_password,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        token = response.json()["access_token"]
        assert isinstance(token, str)
        self._token = token
        self._token_at = time.monotonic()
        return token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token()}"}

    def wait_ready(self, timeout: float = 120.0) -> bool:
        deadline = time.monotonic() + timeout
        url = f"{self.base}/realms/{self.realm}/.well-known/openid-configuration"
        while time.monotonic() < deadline:
            try:
                if httpx.get(url, timeout=5.0).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(2.0)
        return False

    def client(self) -> dict:
        response = httpx.get(
            f"{self.base}/admin/realms/{self.realm}/clients",
            params={"clientId": CLIENT_ID},
            headers=self._headers(),
            timeout=20.0,
        )
        response.raise_for_status()
        clients = response.json()
        if not clients:
            raise RuntimeError(f"client {CLIENT_ID!r} not found in realm {self.realm!r}")
        return clients[0]

    def rotate_client_secret(self) -> str:
        client = self.client()
        secret = secrets.token_urlsafe(32)
        payload = {"clientId": client["clientId"], "secret": secret}
        response = httpx.put(
            f"{self.base}/admin/realms/{self.realm}/clients/{client['id']}",
            json=payload,
            headers=self._headers(),
            timeout=20.0,
        )
        response.raise_for_status()
        return secret

    def get_user(self, username: str) -> dict:
        response = httpx.get(
            f"{self.base}/admin/realms/{self.realm}/users",
            params={"username": username, "exact": "true"},
            headers=self._headers(),
            timeout=20.0,
        )
        response.raise_for_status()
        users = response.json()
        if not users:
            raise RuntimeError(f"user {username!r} not found")
        return users[0]

    def set_password(self, username: str, password: str) -> None:
        user = self.get_user(username)
        response = httpx.put(
            f"{self.base}/admin/realms/{self.realm}/users/{user['id']}/reset-password",
            json={"type": "password", "value": password, "temporary": False},
            headers=self._headers(),
            timeout=20.0,
        )
        response.raise_for_status()


# --------------------------------------------------------------------------
# Real OIDC authorization-code + PKCE flow
# --------------------------------------------------------------------------


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def keycloak_authorization_code(username: str, password: str) -> tuple[str, str]:
    """Drive a real authorization-code + PKCE login. Returns (code, code_verifier)."""
    discovery = httpx.get(
        f"{KEYCLOAK_BASE}/realms/{REALM}/.well-known/openid-configuration", timeout=20.0
    )
    discovery.raise_for_status()
    doc = discovery.json()
    auth_endpoint = doc["authorization_endpoint"]
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)

    with httpx.Client(follow_redirects=False, timeout=30.0) as client:
        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid profile email",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        page = client.get(auth_endpoint, params=params)
        page.raise_for_status()
        action = _form_action(page.text)
        # Keycloak marks its login cookies Secure; a standards-compliant client
        # therefore will not replay them over plain HTTP, and the login POST then
        # fails with "Restart login cookie not found". This acceptance environment
        # is explicitly loopback HTTP, so the session cookies are replayed
        # explicitly on the POST.
        cookie_header = "; ".join(
            f"{cookie.name}={cookie.value}" for cookie in client.cookies.jar
        )
        form = client.post(
            action,
            data={"username": username, "password": password},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": cookie_header,
            },
        )
        location = form.headers.get("location") or ""
        if not location:
            raise RuntimeError(
                f"login for {username!r} produced no redirect "
                f"(status {form.status_code}); check credentials and realm state"
            )
        query = httpx.URL(location).params
        if query.get("error"):
            raise RuntimeError(f"provider error for {username!r}: {query.get('error')}")
        code = query.get("code")
        if not code:
            raise RuntimeError(f"no authorization code in redirect for {username!r}")
        return code, verifier


def _form_action(html: str) -> str:
    import re

    match = re.search(r'<form[^>]+action="([^"]+)"', html)
    if not match:
        raise RuntimeError("could not locate the provider login form action")
    return match.group(1).replace("&amp;", "&")


# --------------------------------------------------------------------------
# OpenJM API client
# --------------------------------------------------------------------------


class OpenJM:
    """A session-authenticated OpenJM client for one persona."""

    def __init__(self, session_token: str, principal: dict | None = None) -> None:
        self.base = BACKEND
        self.session = session_token
        self.principal = principal or {}

    @classmethod
    def login(cls, username: str, password: str) -> "OpenJM":
        code, verifier = keycloak_authorization_code(username, password)
        response = httpx.post(
            f"{BACKEND}/api/auth/oidc/callback",
            json={"code": code, "code_verifier": verifier, "redirect_uri": REDIRECT_URI},
            timeout=30.0,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"OIDC callback failed for {username!r}: "
                f"{response.status_code} {response.text[:300]}"
            )
        session = response.json()["token"]
        instance = cls(session)
        instance.principal = instance.get("/api/auth/me").json()
        return instance

    def _headers(self, tenant: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.session}"}
        if tenant:
            headers["X-OpenJM-Tenant"] = tenant
        return headers

    def get(self, path: str, tenant: str | None = None) -> httpx.Response:
        return httpx.get(f"{self.base}{path}", headers=self._headers(tenant), timeout=30.0)

    def post(self, path: str, json_body=None, tenant: str | None = None) -> httpx.Response:
        return httpx.post(
            f"{self.base}{path}", json=json_body, headers=self._headers(tenant), timeout=60.0
        )

    def patch(self, path: str, json_body=None, tenant: str | None = None) -> httpx.Response:
        return httpx.patch(
            f"{self.base}{path}", json=json_body, headers=self._headers(tenant), timeout=30.0
        )

    def upload_document(self, name: str, content: bytes, mime: str = "text/markdown") -> dict:
        response = httpx.post(
            f"{self.base}/api/knowledge/documents",
            headers=self._headers(),
            files={"file": (name, content, mime)},
            timeout=300.0,
        )
        response.raise_for_status()
        return response.json()

    def chat(self, message: str, mode: str = "chat", tenant: str | None = None) -> dict:
        response = self.post("/api/chat", {"message": message, "mode": mode}, tenant=tenant)
        return {"status_code": response.status_code, "body": _safe_json(response)}

    def chat_slow(self, message: str, mode: str = "chat", tenant: str | None = None,
                  timeout: float = 300.0) -> dict:
        response = httpx.post(
            f"{self.base}/api/chat", json={"message": message, "mode": mode},
            headers=self._headers(tenant), timeout=timeout,
        )
        return {"status_code": response.status_code, "body": _safe_json(response)}


def _safe_json(response: httpx.Response):
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return response.text[:500]


def ledger_path() -> Path:
    return artifacts_dir() / "seed_ledger.json"


def load_ledger() -> dict:
    return json.loads(ledger_path().read_text())


def save_ledger(data: dict) -> None:
    ledger_path().write_text(json.dumps(data, indent=2, sort_keys=True))
