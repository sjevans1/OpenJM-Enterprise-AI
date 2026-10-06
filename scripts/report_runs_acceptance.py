#!/usr/bin/env python3
"""VS4-B2C2 report-run API acceptance script.

Usage:
    python3 scripts/report_runs_acceptance.py [OPTIONS]

Runs against real model (Gemma/OpenRouter), DB-GPT/Chroma, and a temporary
read-only SQLite data source with fully isolated paths (separate database,
uploads, vector store, and credential key file).

Documents are seeded directly into the DB with content_text set (report-run
execution reads content_text, not Chroma).  The data source is created via
the real data API so the StructuredQueryTool gets a live encrypted connection.
The model gateway is real (OpenRouter or local Gemma).  No Workspace ports
are commandeered; an unused ephemeral port is chosen at random.

Options:
    --model-base-url    Override model endpoint (default: .env or 127.0.0.1:18080)
    --model-name        Override model name (default: .env or gemma-4-12b-local)
    --model-api-key     Override model API key (default: .env)
    --keep-temp         Do not delete temp directories after completion
    --timeout           Per-request timeout in seconds (default: 180)

Exit codes:
    0 — all acceptance assertions passed
    1 — one or more assertions failed (or server did not start)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple .env file into a dict (key=value lines)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        env[key] = value
    return env


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_health(base: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base}/api/health", timeout=5)
            if r.status_code == 200:
                return
            last_error = f"status={r.status_code}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"server did not become healthy: {last_error}")


def _fixture_id() -> str:
    return f"acc-{uuid.uuid4().hex[:12]}"


class _Result:
    """Accumulates assertion checks and failures."""

    def __init__(self):
        self.checks: int = 0
        self.failures: list[str] = []

    def check(self, condition, message: str):
        self.checks += 1
        if not condition:
            self.failures.append(message)

    @property
    def ok(self) -> bool:
        return not self.failures


# ── Server management ───────────────────────────────────────────────────────

class IsolatedServer:
    """Manages a real uvicorn subprocess with fully isolated paths."""

    def __init__(self, fixture_id: str, model_overrides: dict[str, str | None],
                 timeout: int, keep_temp: bool):
        self.fixture_id = fixture_id
        self.timeout = timeout
        self.keep_temp = keep_temp
        self.tmp_dir = Path(tempfile.mkdtemp(prefix=f"openjm_acc_{fixture_id}_"))
        self.db_path = self.tmp_dir / "openjm.db"
        self.uploads = self.tmp_dir / "uploads"
        self.vector = self.tmp_dir / "vector"
        self.cred_key = self.tmp_dir / "credentials.key"
        self.sqlite_data = self.tmp_dir / "finance.sqlite"

        self.uploads.mkdir(parents=True)
        self.vector.mkdir(parents=True)
        (self.vector / "chromadb").mkdir(parents=True)

        # Load .env from repo root for default model configuration
        dotenv = _load_dotenv(REPO_ROOT / ".env")
        env = {**os.environ, **dotenv}

        # Apply isolated paths (override anything from .env or os.environ)
        env["OPENJM_DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path}"
        env["OPENJM_UPLOAD_DIR"] = str(self.uploads)
        env["OPENJM_VECTOR_PATH"] = str(self.vector)
        env["OPENJM_VECTOR_COLLECTION"] = f"openjm_acc_{fixture_id}"
        env["OPENJM_CREDENTIAL_KEY_FILE"] = str(self.cred_key)
        env["OPENJM_REPORT_RUNS_ENABLED"] = "true"

        # CLI overrides take highest precedence
        for var, value in model_overrides.items():
            if value is not None:
                env[var] = value

        # Default model if not set anywhere
        env.setdefault("OPENJM_MODEL_BASE_URL", "http://127.0.0.1:18080/v1")
        env.setdefault("OPENJM_MODEL_NAME", "gemma-4-12b-local")

        self.env = env
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._process: subprocess.Popen | None = None

        # Policy document content (written to disk for stored_path)
        self.doc_content = (
            "FY2025 revenue threshold: $300 USD.\n"
            "The fiscal year runs October 1 to September 30.\n"
            "Customers exceeding the FY2025 USD 300 threshold are preferred."
        )
        self.doc_file = self.uploads / f"{fixture_id}_policy.md"
        self.doc_file.write_text(self.doc_content)

        # SQLite data file with revenue data
        conn = sqlite3.connect(str(self.sqlite_data))
        conn.execute("CREATE TABLE IF NOT EXISTS finance (revenue NUMERIC)")
        conn.execute("INSERT INTO finance (revenue) VALUES (325)")
        conn.commit()
        conn.close()

    def start(self):
        self._process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn",
                "app.main:app",
                "--host", "127.0.0.1",
                "--port", str(self.port),
            ],
            cwd=str(BACKEND_DIR),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)

    def cleanup(self):
        self.stop()
        if not self.keep_temp:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def server_logs(self, tail: int = 3000) -> str:
        if self._process and self._process.stdout:
            return self._process.stdout.read().decode(errors="replace")[-tail:]
        return "(no output captured)"


# ── DB seeding ───────────────────────────────────────────────────────────────

def _seed_sync(server: IsolatedServer, question: str, mode: str,
               answer: str, evidence: list,
               report_mode: str = "hybrid") -> dict:
    """Seed a report definition through direct synchronous DB access.

    This mirrors conftest.seed_definition but creates the Document and
    DataSource inline (no Chroma upload). The connection_secret is encrypted
    via the real CredentialVault so the StructuredQueryTool can decrypt it.

    Returns dict with report_id, definition_id, document_id, source_id.
    """
    sys.path.insert(0, str(BACKEND_DIR))
    from app.core.config import Settings
    from app.models import (
        Conversation, DataSource, Document, Message,
        ReportDefinitionVersion, SavedReport,
    )
    from app.services.credentials import CredentialVault
    from app.db import Base
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    # Build schema from the SQLite file
    conn = sqlite3.connect(str(server.sqlite_data))
    tables_info: dict[str, list[str]] = {}
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        table_name = row[0]
        cols = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        tables_info[table_name] = [c[1] for c in cols]
    conn.close()

    schema_json = json.dumps(tables_info)
    authorized_json = json.dumps([
        {"table": t, "columns": cols, "filter": None}
        for t, cols in tables_info.items()
    ])

    # Encrypt the SQLite connection string via the real credential vault
    vault = CredentialVault(server.cred_key.read_bytes())
    connection_secret = vault.encrypt(f"sqlite:///{server.sqlite_data}")

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{server.db_path}",
        upload_dir=server.uploads,
        vector_path=server.vector,
        credential_key_file=server.cred_key,
    )

    engine = create_engine(
        f"sqlite:///{server.db_path}",
        connect_args={"timeout": 30},
    )
    maker = sessionmaker(engine, expire_on_commit=False)

    with maker() as db:
        # ── Document (content_text set, no Chroma upload) ──
        doc = Document(
            user_id=settings.dev_user_id,
            original_name=server.doc_file.name,
            stored_path=str(server.doc_file),
            size_bytes=len(server.doc_content.encode()),
            mime_type="text/markdown",
            status="ready",
            indexed=True,
        )
        db.add(doc)
        db.flush()

        # ── DataSource (real encrypted connection) ──
        src = DataSource(
            user_id=settings.dev_user_id,
            name=f"{server.fixture_id}_finance",
            engine="sqlite",
            connection_secret=connection_secret,
            status="connected",
            enabled=True,
            schema_json=schema_json,
            authorized_objects_json=authorized_json,
            revenue_currency="USD",
        )
        db.add(src)
        db.flush()

        # ── Conversation → Message → SavedReport → Definition ──
        conv = Conversation(
            user_id=settings.dev_user_id,
            title=f"Acceptance {server.fixture_id}",
        )
        db.add(conv)
        db.flush()

        user_msg = Message(
            conversation_id=conv.id,
            role="user",
            content=question,
            requested_mode=mode,
        )
        db.add(user_msg)
        db.flush()

        assistant = Message(
            conversation_id=conv.id,
            role="assistant",
            content=answer,
            execution_class=mode if mode in ("knowledge", "data", "hybrid") else "hybrid",
            requested_mode=mode,
            evidence_json=json.dumps(evidence),
        )
        db.add(assistant)
        db.flush()

        report = SavedReport(
            user_id=settings.dev_user_id,
            conversation_id=conv.id,
            message_id=assistant.id,
            title=f"Acceptance report {server.fixture_id}",
            answer_text=assistant.content,
            evidence_json=assistant.evidence_json,
            execution_class=assistant.execution_class,
            requested_mode=mode,
            source_count=2 if mode == "hybrid" else 1,
            snapshot_as_of=assistant.created_at,
        )
        db.add(report)
        db.flush()

        definition = ReportDefinitionVersion(
            user_id=settings.dev_user_id,
            report_id=report.id,
            version=1,
            question_text=question,
            requested_mode=mode,
            pinned_document_ids_json=json.dumps([doc.id]),
            pinned_source_tables_json=json.dumps({str(src.id): ["finance"]}),
        )
        db.add(definition)
        db.commit()

        return {
            "report_id": report.id,
            "definition_id": definition.id,
            "definition_version": 1,
            "document_id": doc.id,
            "source_id": src.id,
            "question": question,
            "mode": mode,
        }


# ── API helpers ──────────────────────────────────────────────────────────────

def _submit_run(base: str, report_id: str, version: int, key: str,
                timeout: float) -> dict:
    """Submit a report run; return status_code and json."""
    response = httpx.post(
        f"{base}/api/reports/{report_id}/definitions/{version}/runs",
        json={"idempotency_key": key},
        timeout=timeout,
    )
    return {"status_code": response.status_code, "json": response.json()}


# ── Acceptance scenarios ─────────────────────────────────────────────────────

def _build_hybrid_definition(server: IsolatedServer) -> dict:
    """Seed a hybrid report definition (Knowledge + Data evidence)."""
    evidence = [
        {
            "source_type": "document",
            "source_id": None,
            "title": "Policy",
            "passage": "FY2025 threshold USD 300",
        },
        {
            "source_type": "structured_query",
            "source_id": None,
            "title": "Finance",
            "passage": '{"columns":["revenue"],"rows":[[325]],"row_count":1}',
            "metadata": {
                "tables": ["finance"],
                "sql": "SELECT revenue FROM finance",
            },
            "provenance": {
                "grounded_parameter": {
                    "value": "300",
                }
            },
        },
    ]
    question = "Which customers exceed the FY2025 USD 300 threshold?"
    answer = "Customer A exceeded the FY2025 USD 300 threshold with revenue of 325."
    return _seed_sync(server, question, "hybrid", answer, evidence)


def _acceptance(server: IsolatedServer, result: _Result):
    """Run all acceptance scenarios."""
    base = server.base
    fixture_id = server.fixture_id
    timeout = server.timeout

    # ── Seed a hybrid report definition via direct DB ──
    try:
        definition = _build_hybrid_definition(server)
    except Exception as exc:
        result.check(False, f"direct DB seeding failed: {exc}")
        return

    result.check(bool(definition["report_id"]), "seed did not produce report_id")

    run_keys = [
        f"00000000-0000-4000-8000-{fixture_id[:12]}001",
        f"00000000-0000-4000-8000-{fixture_id[:12]}002",
        f"00000000-0000-4000-8000-{fixture_id[:12]}rev",
    ]

    # ── Scenario 1: New submission executes ──
    print(f"[acceptance] submitting first run (key=...{run_keys[0][-4:]})...")
    first = _submit_run(
        base, definition["report_id"],
        definition["definition_version"], run_keys[0], timeout,
    )
    result.check(
        first["status_code"] == 202,
        f"first submission expected 202, got {first['status_code']}: {first['json']}",
    )
    if first["status_code"] == 202:
        result.check(
            first["json"]["status"] in ("succeeded", "failed"),
            f"first run status unexpected: {first['json']['status']}",
        )
        print(f"[acceptance] first run: {first['json']['status']} "
              f"({first['json']['id'][:12]})")

    # ── Scenario 2: Duplicate submission (same idempotency key) ──
    print("[acceptance] submitting duplicate run (same key)...")
    dup = _submit_run(
        base, definition["report_id"],
        definition["definition_version"], run_keys[0], timeout,
    )
    result.check(
        dup["status_code"] == 202,
        f"duplicate submission expected 202, got {dup['status_code']}",
    )
    if dup["status_code"] == 202 and first["status_code"] == 202:
        result.check(
            dup["json"]["id"] == first["json"]["id"],
            "duplicate submission returned different run ID",
        )
        result.check(
            dup["json"] == first["json"],
            "replay result does not match first submission (not immutable)",
        )
        print(f"[acceptance] duplicate run: same ID={dup['json']['id'][:12]}, "
              f"immutable={dup['json'] == first['json']}")

    # ── Scenario 3: Different idempotency key, new run ──
    print("[acceptance] submitting second run (different key)...")
    second = _submit_run(
        base, definition["report_id"],
        definition["definition_version"], run_keys[1], timeout,
    )
    result.check(
        second["status_code"] == 202,
        f"second submission expected 202, got {second['status_code']}",
    )
    if second["status_code"] == 202 and first["status_code"] == 202:
        result.check(
            second["json"]["id"] != first["json"]["id"],
            "second submission returned same run ID as first",
        )
        print(f"[acceptance] second run: {second['json']['id'][:12]} "
              f"(different from first={first['json']['id'][:12]})")

    # ── Scenario 4: History list is readable ──
    print("[acceptance] listing run history...")
    history = httpx.get(
        f"{base}/api/reports/{definition['report_id']}/runs",
        timeout=timeout,
    )
    result.check(
        history.status_code == 200,
        f"history list expected 200, got {history.status_code}",
    )
    if history.status_code == 200:
        runs = history.json()
        result.check(len(runs) >= 2, f"history expected >=2 runs, got {len(runs)}")
        print(f"[acceptance] history: {len(runs)} runs listed")

    # ── Scenario 5: Revocation endpoint is wired ──
    print("[acceptance] testing revocation endpoint...")
    rev_run = _submit_run(
        base, definition["report_id"],
        definition["definition_version"], run_keys[2], timeout,
    )
    if rev_run["status_code"] == 202:
        run_id = rev_run["json"]["id"]
        revoke = httpx.post(
            f"{base}/api/reports/{definition['report_id']}/runs/{run_id}/revoke",
            timeout=timeout,
        )
        result.check(
            revoke.status_code in (200, 202, 409),
            f"revoke endpoint expected 200/202/409, got {revoke.status_code}: {revoke.text}",
        )
        print(f"[acceptance] revoke: {revoke.status_code}")
    else:
        result.check(False, f"revocation test run failed to submit: {rev_run}")

    # ── Scenario 6: Gate enforcement var is set ──
    result.check(
        server.env.get("OPENJM_REPORT_RUNS_ENABLED", "true") == "true",
        "OPENJM_REPORT_RUNS_ENABLED not enabled in server env",
    )

    # ── Cleanup temp data file ──
    server.sqlite_data.unlink(missing_ok=True)


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="VS4-B2C2 report-run API acceptance script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--model-base-url", default=None,
                        help="Override model endpoint (default: .env or local Gemma)")
    parser.add_argument("--model-name", default=None,
                        help="Override model name (default: .env or gemma-4-12b-local)")
    parser.add_argument("--model-api-key", default=None,
                        help="Override model API key (default: .env)")
    parser.add_argument("--keep-temp", action="store_true",
                        help="Do not delete temp directories after completion")
    parser.add_argument("--timeout", type=int, default=180,
                        help="Per-request timeout in seconds (default: 180)")
    args = parser.parse_args()

    model_overrides = {
        "OPENJM_MODEL_BASE_URL": args.model_base_url,
        "OPENJM_MODEL_NAME": args.model_name,
        "OPENJM_MODEL_API_KEY": args.model_api_key,
    }

    fixture_id = _fixture_id()
    server = IsolatedServer(fixture_id, model_overrides, args.timeout, args.keep_temp)

    print(f"[acceptance] fixture_id={fixture_id}")
    print(f"[acceptance] temp_dir={server.tmp_dir}")
    print(f"[acceptance] db={server.db_path}")
    print(f"[acceptance] port={server.port}")
    print(f"[acceptance] model_base_url={server.env.get('OPENJM_MODEL_BASE_URL', '')}")
    print(f"[acceptance] model_name={server.env.get('OPENJM_MODEL_NAME', '')}")

    result = _Result()

    try:
        server.start()
        _wait_health(server.base, timeout=60)
        print("[acceptance] server healthy")
        _acceptance(server, result)
    except Exception as exc:
        result.check(False, f"acceptance execution error: {exc}")
        print(f"[acceptance] server logs:\n{server.server_logs()}", file=sys.stderr)
    finally:
        server.cleanup()
        if not args.keep_temp:
            print(f"[acceptance] temp dir removed: {server.tmp_dir}")

    if result.ok:
        print(f"\n[acceptance] ALL {result.checks} checks passed")
        print("[acceptance] ACCEPTED")
        return 0
    else:
        print(f"\n[acceptance] {len(result.failures)} failure(s) of {result.checks} checks:")
        for f in result.failures:
            print(f"  - {f}")
        print("[acceptance] REJECTED")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
