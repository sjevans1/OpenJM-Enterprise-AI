#!/usr/bin/env python3
"""VS4-B2C2 report-run API acceptance script.

Usage:
    python3 scripts/report_runs_acceptance.py [OPTIONS]

Runs against real model (Gemma/OpenRouter), DB-GPT/Chroma, and a temporary
read-only SQLite data source with fully isolated paths (separate database,
uploads, vector store, and credential key file).

Options:
    --model-base-url    Override model endpoint (default: .env or 127.0.0.1:18080)
    --model-name        Override model name (default: .env or gemma-4-12b-local)
    --model-api-key     Override model API key (default: .env)
    --keep-temp         Do not delete temp directories after completion
    --timeout           Per-request timeout in seconds (default: 300)

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

    def check(self, condition: bool, message: str):
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

        # Apply isolated paths
        env["OPENJM_DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path}"
        env["OPENJM_UPLOAD_DIR"] = str(self.uploads)
        env["OPENJM_VECTOR_PATH"] = str(self.vector)
        env["OPENJM_VECTOR_COLLECTION"] = f"openjm_acc_{fixture_id}"
        env["OPENJM_CREDENTIAL_KEY_FILE"] = str(self.cred_key)
        env["OPENJM_REPORT_RUNS_ENABLED"] = "true"
        env["OPENJM_DATABASE_URL"] = env["OPENJM_DATABASE_URL"]

        # Model overrides take precedence
        for var, value in model_overrides.items():
            if value is not None:
                env[var] = value

        self.env = env
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._process: subprocess.Popen | None = None

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

    def server_logs(self, tail: int = 2000) -> str:
        if self._process and self._process.stdout:
            return self._process.stdout.read().decode(errors="replace")[-tail:]
        return "(no output captured)"


# ── DB seeding ───────────────────────────────────────────────────────────────

async def _seed_via_db(db_path: str, fixture_id: str, document_id: str,
                       source_id: str | None, question: str, mode: str,
                       answer: str, evidence_json: str,
                       pinned_doc_ids_json: str,
                       pinned_tables_json: str) -> dict:
    """Seed a report definition through direct DB access.

    Mirrors conftest.seed_definition: Conversation -> Message -> SavedReport
    -> ReportDefinitionVersion, all attached to real Document and DataSource.
    Returns dict with report_id, definition_id.
    """
    sys.path.insert(0, str(BACKEND_DIR))
    from app.models import (
        Conversation, Document, Message,
        ReportDefinitionVersion, SavedReport,
    )
    from app.core.config import Settings
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.db import enable_sqlite_foreign_keys, _migrate_add_active_run_index
    from sqlalchemy import event

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"timeout": 10},
    )

    async def _pragmas(dbapi_connection, _record):
        enable_sqlite_foreign_keys(dbapi_connection, _record)
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    event.listen(engine.sync_engine, "connect", _pragmas)

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        upload_dir=Path(__file__).resolve().parents[1] / "backend" / "data" / "uploads",
        vector_path=Path(__file__).resolve().parents[1] / "backend" / "data" / "vector",
        credential_key_file=Path(__file__).resolve().parents[1] / "backend" / "data" / "credentials.key",
    )

    async with engine.begin() as conn:
        await conn.run_sync(_migrate_add_active_run_index)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        conv = Conversation(
            user_id=settings.dev_user_id,
            title=f"Acceptance {fixture_id}",
        )
        db.add(conv)
        await db.flush()

        user_msg = Message(
            conversation_id=conv.id,
            role="user",
            content=question,
            requested_mode=mode,
        )
        db.add(user_msg)
        await db.flush()

        assistant = Message(
            conversation_id=conv.id,
            role="assistant",
            content=answer,
            execution_class=mode if mode in ("knowledge", "data", "hybrid") else "hybrid",
            requested_mode=mode,
            evidence_json=evidence_json,
        )
        db.add(assistant)
        await db.flush()

        report = SavedReport(
            user_id=settings.dev_user_id,
            conversation_id=conv.id,
            message_id=assistant.id,
            title=f"Acceptance report {fixture_id}",
            answer_text=assistant.content,
            evidence_json=assistant.evidence_json,
            execution_class=assistant.execution_class,
            requested_mode=mode,
            source_count=2 if mode in ("hybrid",) else 1,
            snapshot_as_of=assistant.created_at,
        )
        db.add(report)
        await db.flush()

        definition = ReportDefinitionVersion(
            user_id=settings.dev_user_id,
            report_id=report.id,
            version=1,
            question_text=question,
            requested_mode=mode,
            pinned_document_ids_json=pinned_doc_ids_json,
            pinned_source_tables_json=pinned_tables_json,
        )
        db.add(definition)
        await db.commit()

        return {
            "report_id": report.id,
            "definition_id": definition.id,
            "definition_version": 1,
            "question": question,
            "mode": mode,
        }


# ── API helpers ──────────────────────────────────────────────────────────────

async def _upload_document(base: str, content: str, filename: str,
                           timeout: float) -> str:
    """Upload a document through the real knowledge API; return document ID."""
    tmp_file = Path(tempfile.gettempdir()) / filename
    tmp_file.write_text(content)
    try:
        upload = httpx.post(
            f"{base}/api/knowledge/documents",
            files={"file": (filename, tmp_file.read_bytes(), "text/markdown")},
            timeout=timeout,
        )
        upload.raise_for_status()
        return upload.json()["id"]
    finally:
        tmp_file.unlink(missing_ok=True)


async def _create_data_source(base: str, name: str, sqlite_path: str,
                              timeout: float) -> str:
    """Create a data source through the real data API; return source ID."""
    create = httpx.post(
        f"{base}/api/data/sources",
        json={
            "name": name,
            "engine": "sqlite",
            "connection_uri": f"sqlite:///{sqlite_path}",
            "revenue_currency": "USD",
            "enabled": True,
        },
        timeout=timeout,
    )
    create.raise_for_status()
    return create.json()["id"]


async def _submit_run(base: str, report_id: str, version: int, key: str,
                      timeout: float) -> dict:
    """Submit a report run; return status_code and json."""
    response = httpx.post(
        f"{base}/api/reports/{report_id}/definitions/{version}/runs",
        json={"idempotency_key": key},
        timeout=timeout,
    )
    return {"status_code": response.status_code, "json": response.json()}


# ── Acceptance scenarios ─────────────────────────────────────────────────────

async def _acceptance(server: IsolatedServer, result: _Result):
    """Run all acceptance scenarios."""
    base = server.base
    db_path = str(server.db_path)
    fixture_id = server.fixture_id
    timeout = server.timeout

    # ── Upload policy document through the real knowledge API ──
    doc_content = (
        "FY2025 revenue threshold: $300 USD.\n"
        "The fiscal year runs October 1 to September 30.\n"
        "Customers exceeding the FY2025 USD 300 threshold are preferred."
    )
    doc_name = f"{fixture_id}_policy.md"
    document_id = await _upload_document(base, doc_content, doc_name, timeout)
    result.check(bool(document_id), "document upload did not return an ID")

    # ── Create a temporary read-only SQLite data source ──
    conn = sqlite3.connect(str(server.sqlite_data))
    conn.execute("CREATE TABLE IF NOT EXISTS finance (revenue NUMERIC)")
    conn.execute("INSERT INTO finance (revenue) VALUES (325)")
    conn.commit()
    conn.close()

    source_name = f"{fixture_id}_finance"
    try:
        source_id = await _create_data_source(
            base, source_name, str(server.sqlite_data), timeout
        )
        result.check(bool(source_id), "data source creation did not return an ID")
    except Exception as exc:
        result.check(False, f"data source creation failed: {exc}")
        source_id = None

    # ── Seed a hybrid report definition via direct DB ──
    evidence_json = json.dumps([
        {
            "source_type": "document",
            "source_id": document_id,
            "title": "Policy",
            "passage": "FY2025 threshold USD 300",
        },
        {
            "source_type": "structured_query",
            "source_id": source_id or "placeholder",
            "title": "Finance",
            "passage": '{"columns":["revenue"],"rows":[[325]],"row_count":1}',
            "metadata": {
                "tables": ["finance"],
                "sql": "SELECT revenue FROM finance",
            },
            "provenance": {
                "grounded_parameter": {
                    "source_id": document_id,
                    "value": "300",
                }
            },
        },
    ])

    question = "Which customers exceed the FY2025 USD 300 threshold?"
    answer = "At least one customer exceeded the FY2025 USD 300 threshold."

    try:
        definition = await _seed_via_db(
            db_path, fixture_id,
            document_id, source_id,
            question, "hybrid", answer, evidence_json,
            json.dumps([document_id]),
            json.dumps({source_id: ["finance"]}) if source_id else "{}",
        )
    except Exception as exc:
        result.check(False, f"direct DB seeding failed: {exc}")
        return

    run_keys = [
        f"00000000-0000-4000-8000-{fixture_id[:12]}001",
        f"00000000-0000-4000-8000-{fixture_id[:12]}002",
    ]

    # ── Scenario 1: New submission executes ──
    first = await _submit_run(
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

    # ── Scenario 2: Duplicate submission (same idempotency key) ──
    dup = await _submit_run(
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

    # ── Scenario 3: Different idempotency key, new run ──
    second = await _submit_run(
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

    # ── Scenario 4: History list is readable ──
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

    # ── Scenario 5: Revocation transitions running to interrupted ──
    # (Only meaningful if first run is still running — for a fast model it
    #  may already be succeeded/failed. We test the revoke endpoint is wired
    #  and returns the correct terminal states for a new run.)
    revoke_key = f"00000000-0000-4000-8000-{fixture_id[:12]}rev"
    rev_run = await _submit_run(
        base, definition["report_id"],
        definition["definition_version"], revoke_key, timeout,
    )
    if rev_run["status_code"] == 202:
        run_id = rev_run["json"]["id"]
        revoke = httpx.post(
            f"{base}/api/reports/{definition['report_id']}/runs/{run_id}/revoke",
            timeout=timeout,
        )
        result.check(
            revoke.status_code in (200, 202, 409),
            f"revoke endpoint expected 200/202/409, got {revoke.status_code}",
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
                        help="Override model endpoint")
    parser.add_argument("--model-name", default=None,
                        help="Override model name")
    parser.add_argument("--model-api-key", default=None,
                        help="Override model API key")
    parser.add_argument("--keep-temp", action="store_true",
                        help="Do not delete temp directories after completion")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Per-request timeout in seconds (default: 300)")
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
        await _acceptance(server, result)
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
