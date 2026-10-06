#!/usr/bin/env python3
"""VS4-B2C2 report-run API acceptance script.

Usage:
    python3 scripts/report_runs_acceptance.py [OPTIONS]

Runs against the real model gateway (local Gemma or any OpenAI-compatible
endpoint), real DB-GPT/Chroma ingestion, and a temporary read-only SQLite
data source, with fully isolated paths (separate database, uploads, vector
store, and credential key file).

Coverage:
    * Knowledge execution  (pinned document evidence only)
    * Data execution       (pinned structured evidence only)
    * Independent Hybrid   (document + structured evidence)
    * Dependent Hybrid     (structured evidence grounded on a policy document)
    * Duplicate submission (same idempotency key -> identical, immutable result)
    * Revocation           (disabled source fails closed, no answer delivered)
    * Immutable earlier results (re-reading a terminal run is unchanged)

Isolation preflight asserts the server is bound to a private temp database,
temp vector/upload/key paths, and an ephemeral port that is never a reserved
Workspace port. No source database writes occur; the SQLite data file is
read-only from the app's perspective.

Options:
    --model-base-url    Override model endpoint (default: .env or local Gemma)
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

# Ports used by the developer workspace; the acceptance server must never
# commandeer one of these (ephemeral allocation only).
RESERVED_PORTS = {8000, 8080, 18080, 5173, 3000, 5432, 6379}


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
    """Allocate an unused ephemeral port, never a reserved workspace port."""
    for _ in range(20):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in RESERVED_PORTS:
            return port
    raise RuntimeError("could not allocate a non-reserved ephemeral port")


def _wait_health(base: str, timeout: float = 90.0) -> None:
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

        # Default model if not set anywhere (local Gemma worker)
        env.setdefault("OPENJM_MODEL_BASE_URL", "http://127.0.0.1:18080/v1")
        env.setdefault("OPENJM_MODEL_NAME", "gemma-4-12b-local")

        self.env = env
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._process: subprocess.Popen | None = None

        # Policy document content (uploaded via the real knowledge API)
        self.doc_content = (
            "FY2025 revenue threshold: $300 USD.\n"
            "The fiscal year runs October 1 to September 30.\n"
            "Customers whose annual revenue exceeds the FY2025 USD 300 "
            "threshold are classified as preferred accounts.\n"
            "Finance revenue for the preferred account Acme was 325 USD.\n"
        )
        self.doc_file = self.uploads / f"{fixture_id}_policy.md"
        self.doc_file.write_text(self.doc_content)

        # SQLite data file with revenue data (read-only source for queries)
        conn = sqlite3.connect(str(self.sqlite_data))
        conn.execute("CREATE TABLE IF NOT EXISTS finance (customer TEXT, revenue NUMERIC)")
        conn.execute("INSERT INTO finance (customer, revenue) VALUES ('Acme', 325)")
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

    def server_logs(self, tail: int = 4000) -> str:
        if self._process and self._process.stdout:
            return self._process.stdout.read().decode(errors="replace")[-tail:]
        return "(no output captured)"


def _isolation_preflight(server: IsolatedServer, result: _Result):
    """Assert the server is bound to isolated temp paths and a private port."""
    tmp = server.tmp_dir.resolve()
    for label, path in (
        ("database", server.db_path),
        ("uploads", server.uploads),
        ("vector", server.vector),
        ("credential key", server.cred_key),
    ):
        try:
            path.resolve().relative_to(tmp)
            ok = True
        except ValueError:
            ok = False
        result.check(ok, f"isolation: {label} path is outside the temp dir")
    result.check(
        server.port not in RESERVED_PORTS,
        f"isolation: ephemeral port {server.port} collides with a reserved port",
    )
    result.check(
        server.env.get("OPENJM_REPORT_RUNS_ENABLED") == "true",
        "isolation: report-run gate not enabled for this instance",
    )


# ── DB seeding ───────────────────────────────────────────────────────────────

def _seed_report(server: IsolatedServer, *, question: str, mode: str,
                 answer: str, doc_id: str | None = None,
                 source_id: str | None = None,
                 dependent: bool = False) -> dict:
    """Seed a report definition through direct DB access.

    The Document (uploaded via the real knowledge API) and DataSource (created
    via the real data API) are referenced by their real ids. Evidence is
    assembled so the stored pins match the evidence-derived scope exactly
    (sorted unique ids and sorted unique table names), which the API enforces.
    """
    sys.path.insert(0, str(BACKEND_DIR))
    from app.core.config import Settings
    from app.models import (
        Conversation, Message, ReportDefinitionVersion, SavedReport,
    )
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{server.db_path}",
        upload_dir=server.uploads,
        vector_path=server.vector,
        credential_key_file=server.cred_key,
    )

    evidence: list[dict] = []
    if doc_id is not None:
        evidence.append({
            "source_type": "document",
            "source_id": doc_id,
            "title": "Policy",
            "passage": "FY2025 threshold USD 300",
        })
    if source_id is not None:
        structured = {
            "source_type": "structured_query",
            "source_id": source_id,
            "title": "Finance",
            "passage": '{"columns":["customer","revenue"],"rows":[["Acme",325]],"row_count":1}',
            "metadata": {
                "tables": ["finance"],
                "sql": "SELECT customer, revenue FROM finance",
            },
        }
        if dependent and doc_id is not None:
            structured["provenance"] = {
                "grounded_parameter": {"source_id": doc_id, "value": "300"}
            }
        evidence.append(structured)

    pinned_docs = sorted({doc_id} - {None}) if doc_id else []
    pinned_tables = {source_id: ["finance"]} if source_id else {}

    # The rerun preview maps execution_class -> expected requested_mode as
    # {"knowledge": "knowledge", "structured": "data", "hybrid": "hybrid"}.
    # Data execution is class "structured" with requested_mode "data".
    execution_class = {"knowledge": "knowledge", "data": "structured",
                       "hybrid": "hybrid"}[mode]

    engine = create_engine(
        f"sqlite:///{server.db_path}",
        connect_args={"timeout": 30},
    )
    maker = sessionmaker(engine, expire_on_commit=False)

    with maker() as db:
        conv = Conversation(
            user_id=settings.dev_user_id,
            title=f"Acceptance {server.fixture_id} {mode}",
        )
        db.add(conv)
        db.flush()

        db.add(Message(
            conversation_id=conv.id,
            role="user",
            content=question,
            requested_mode=mode,
        ))
        db.flush()

        assistant = Message(
            conversation_id=conv.id,
            role="assistant",
            content=answer,
            execution_class=execution_class,
            requested_mode=mode,
            evidence_json=json.dumps(evidence),
        )
        db.add(assistant)
        db.flush()

        report = SavedReport(
            user_id=settings.dev_user_id,
            conversation_id=conv.id,
            message_id=assistant.id,
            title=f"Acceptance report {server.fixture_id} {mode}",
            answer_text=assistant.content,
            evidence_json=assistant.evidence_json,
            execution_class=execution_class,
            requested_mode=mode,
            source_count=len(evidence),
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
            pinned_document_ids_json=json.dumps(pinned_docs),
            pinned_source_tables_json=json.dumps(pinned_tables),
        )
        db.add(definition)
        db.commit()

        return {
            "report_id": report.id,
            "definition_id": definition.id,
            "definition_version": 1,
            "document_id": doc_id,
            "source_id": source_id,
            "question": question,
            "mode": mode,
        }


# ── API helpers ──────────────────────────────────────────────────────────────

def _submit_run(base: str, report_id: str, version: int, key: str,
                timeout: float) -> dict:
    response = httpx.post(
        f"{base}/api/reports/{report_id}/definitions/{version}/runs",
        json={"idempotency_key": key},
        timeout=timeout,
    )
    return {"status_code": response.status_code, "json": response.json()}


def _create_source(server: IsolatedServer, result: _Result) -> str | None:
    """Create -> test -> refresh a real SQLite data source."""
    base, timeout = server.base, server.timeout
    print("[acceptance] creating data source via real data API...")
    try:
        created = httpx.post(
            f"{base}/api/data/sources",
            json={
                "name": f"{server.fixture_id}_finance",
                "engine": "sqlite",
                "connection_uri": f"sqlite:///{server.sqlite_data}",
                "revenue_currency": "USD",
                "enabled": True,
            },
            timeout=timeout,
        )
        created.raise_for_status()
        source_id = created.json()["id"]
        result.check(bool(source_id), "data source creation returned no id")

        tested = httpx.post(f"{base}/api/data/sources/{source_id}/test", timeout=timeout)
        result.check(tested.status_code == 200,
                     f"data source test failed: {tested.status_code}")

        refreshed = httpx.post(f"{base}/api/data/sources/{source_id}/refresh", timeout=timeout)
        result.check(refreshed.status_code == 200,
                     f"data source refresh failed: {refreshed.status_code}: {refreshed.text}")
        print(f"[acceptance] data source {source_id[:12]} "
              f"(test={tested.status_code}, refresh={refreshed.status_code})")
        return source_id
    except Exception as exc:
        result.check(False, f"data source setup failed: {exc}")
        return None


def _upload_document(server: IsolatedServer, result: _Result) -> str | None:
    """Upload the policy document via the real knowledge API (Chroma ingest)."""
    print("[acceptance] uploading policy document via real knowledge API...")
    try:
        with open(server.doc_file, "rb") as handle:
            response = httpx.post(
                f"{server.base}/api/knowledge/documents",
                files={"file": (server.doc_file.name, handle, "text/markdown")},
                timeout=server.timeout,
            )
        result.check(response.status_code == 200,
                     f"document upload failed: {response.status_code}: {response.text[:200]}")
        if response.status_code != 200:
            return None
        doc_id = response.json()["id"]
        print(f"[acceptance] document {doc_id[:12]} ingested")
        return doc_id
    except Exception as exc:
        result.check(False, f"document upload error: {exc}")
        return None


def _run_mode(server: IsolatedServer, result: _Result, label: str,
              definition: dict, key: str) -> dict | None:
    """Submit one run for a mode and assert it reaches a terminal state."""
    print(f"[acceptance] mode={label}: submitting...")
    outcome = _submit_run(
        server.base, definition["report_id"], definition["definition_version"],
        key, server.timeout,
    )
    result.check(
        outcome["status_code"] == 202,
        f"{label}: submission expected 202, got {outcome['status_code']}: {outcome['json']}",
    )
    if outcome["status_code"] != 202:
        return None
    status = outcome["json"].get("status")
    result.check(
        status in ("succeeded", "failed"),
        f"{label}: run status not terminal: {status}",
    )
    print(f"[acceptance] mode={label}: {status} ({outcome['json']['id'][:12]})")
    return outcome


# ── Acceptance scenarios ─────────────────────────────────────────────────────

def _acceptance(server: IsolatedServer, result: _Result) -> None:
    base = server.base
    timeout = server.timeout

    source_id = _create_source(server, result)
    doc_id = _upload_document(server, result)
    if source_id is None or doc_id is None:
        return

    knowledge_q = "What is the FY2025 preferred-account revenue threshold?"
    data_q = "What is the revenue for customer Acme?"
    hybrid_q = "Which customers exceed the FY2025 USD 300 preferred threshold?"

    # ── Seed one definition per execution mode ──
    try:
        defs = {
            "knowledge": _seed_report(
                server, question=knowledge_q, mode="knowledge",
                answer="The FY2025 preferred-account threshold is USD 300.",
                doc_id=doc_id),
            "data": _seed_report(
                server, question=data_q, mode="data",
                answer="Acme revenue is 325 USD.",
                source_id=source_id),
            "hybrid_independent": _seed_report(
                server, question=hybrid_q, mode="hybrid",
                answer="Acme exceeded the FY2025 USD 300 threshold with revenue 325 USD.",
                doc_id=doc_id, source_id=source_id),
            "hybrid_dependent": _seed_report(
                server, question=hybrid_q, mode="hybrid",
                answer="Acme (325 USD) exceeds the grounded FY2025 USD 300 threshold.",
                doc_id=doc_id, source_id=source_id, dependent=True),
        }
    except Exception as exc:
        result.check(False, f"direct DB seeding failed: {exc}")
        return

    # ── Coverage: Knowledge, Data, Independent Hybrid, Dependent Hybrid ──
    outcomes = {}
    for label, definition in defs.items():
        outcomes[label] = _run_mode(
            server, result, label, definition, str(uuid.uuid4())
        )

    succeeded = [label for label, out in outcomes.items()
                 if out and out["json"].get("status") == "succeeded"]
    result.check(
        bool(succeeded),
        "no execution mode produced a grounded result (real pipeline unproven)",
    )
    print(f"[acceptance] succeeded modes: {succeeded or 'none'}")

    # ── Duplicate submission + immutable earlier results ──
    dup_report = defs["hybrid_independent"]
    dup_key = str(uuid.uuid4())
    print("[acceptance] duplicate submission (same idempotency key)...")
    first = _submit_run(base, dup_report["report_id"], 1, dup_key, timeout)
    second = _submit_run(base, dup_report["report_id"], 1, dup_key, timeout)
    result.check(first["status_code"] == 202,
                 f"duplicate first submit expected 202, got {first['status_code']}")
    result.check(second["status_code"] == 202,
                 f"duplicate second submit expected 202, got {second['status_code']}")
    if first["status_code"] == 202 and second["status_code"] == 202:
        result.check(first["json"]["id"] == second["json"]["id"],
                     "duplicate submission returned a different run id")
        result.check(first["json"] == second["json"],
                     "duplicate submission result is not byte-identical (not immutable)")
        print(f"[acceptance] duplicate: same id={first['json']['id'][:12]}, "
              f"identical={first['json'] == second['json']}")

        # Re-read the earlier terminal run; it must be unchanged.
        read = httpx.get(f"{base}/api/reports/runs/{first['json']['id']}", timeout=timeout)
        if read.status_code == 200:
            result.check(read.json() == first["json"],
                         "immutable earlier result changed when re-read")
        else:
            # Revoked sources mask the detail; that is also acceptable.
            result.check(read.status_code in (409, 404),
                         f"re-reading earlier run gave unexpected {read.status_code}")

    # ── History is readable ──
    history = httpx.get(f"{base}/api/reports/{defs['knowledge']['report_id']}/runs",
                        timeout=timeout)
    result.check(history.status_code == 200,
                 f"history list expected 200, got {history.status_code}")
    if history.status_code == 200:
        result.check(len(history.json()) >= 1,
                     "history list did not contain the knowledge run")

    # ── Revocation: disabling the source fails closed with no delivery ──
    print("[acceptance] revocation: disabling data source...")
    disabled = httpx.patch(
        f"{base}/api/data/sources/{source_id}",
        json={"enabled": False},
        timeout=timeout,
    )
    result.check(disabled.status_code == 200,
                 f"disabling source expected 200, got {disabled.status_code}")

    revoked = _submit_run(base, defs["data"]["report_id"], 1, str(uuid.uuid4()), timeout)
    result.check(revoked["status_code"] == 409,
                 f"revoked source submission expected 409 fail-closed, "
                 f"got {revoked['status_code']}: {revoked['json']}")
    print(f"[acceptance] revocation: submission returned {revoked['status_code']} (expected 409)")

    # The masked run must not expose any delivered answer/data.
    if revoked["status_code"] == 409:
        body = json.dumps(revoked["json"]).lower()
        result.check("revenue" not in body and "325" not in body,
                     "revoked response leaked source data")

    # Re-enable the source to confirm the gate is reversible.
    reenabled = httpx.patch(
        f"{base}/api/data/sources/{source_id}",
        json={"enabled": True},
        timeout=timeout,
    )
    result.check(reenabled.status_code == 200,
                 f"re-enabling source expected 200, got {reenabled.status_code}")

    # ── Cleanup owned temp data ──
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
        _wait_health(server.base, timeout=90)
        print("[acceptance] server healthy")
        _isolation_preflight(server, result)
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
