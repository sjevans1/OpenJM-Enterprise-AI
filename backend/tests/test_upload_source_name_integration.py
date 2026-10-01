"""Integration test: customer-facing source_name must be the ORIGINAL upload
filename, never the UUID-prefixed storage filename or a host path.

Boots a real uvicorn subprocess against an isolated temp database/uploads/
vector store (no shared dev data), uploads a fixture through the real
``/api/knowledge/documents`` API, retrieves through the real engine pointed
at the same isolated vector store, and asserts the Evidence metadata
contract. Deterministic: no model/gateway involved (retrieval only).
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURE = BACKEND_DIR / "tests" / "fixtures" / "rag" / "heading_context.md"
UPLOAD_NAME = "phase d filename check.md"  # spaces on purpose: must round-trip


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_health(base: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base}/api/health", timeout=3)
            if response.status_code == 200:
                return
            last_error = f"status={response.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(0.4)
    raise RuntimeError(f"server did not become healthy: {last_error}")


@pytest.fixture(scope="module")
def isolated_server(tmp_path_factory):
    """Real uvicorn subprocess with isolated env; yields (base_url, env)."""

    tmp = tmp_path_factory.mktemp("openjm_upload_it")
    db_path = tmp / "openjm.db"
    uploads = tmp / "uploads"
    vector = tmp / "vector"
    uploads.mkdir(parents=True)
    vector.mkdir(parents=True)
    (vector / "chromadb").mkdir(parents=True)

    port = _free_port()
    env = {
        **os.environ,
        "OPENJM_DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
        "OPENJM_UPLOAD_DIR": str(uploads),
        "OPENJM_VECTOR_PATH": str(vector),
        "OPENJM_CREDENTIAL_KEY_FILE": str(tmp / "credentials.key"),
        # keep the real embedding model; only isolation paths are overridden
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_health(base)
        yield {"base": base, "env": env, "uploads": uploads, "vector": vector}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.asyncio
async def test_evidence_source_name_is_original_filename(isolated_server):
    base = isolated_server["base"]

    # 1. Upload through the real API with a distinctive original filename.
    with open(FIXTURE, "rb") as handle:
        upload = httpx.post(
            f"{base}/api/knowledge/documents",
            files={"file": (UPLOAD_NAME, handle, "text/markdown")},
            timeout=120,
        )
    upload.raise_for_status()
    document = upload.json()
    document_id = document["id"]
    assert document["original_name"] == UPLOAD_NAME
    assert document["status"] == "ready"
    assert document["indexed"] is True

    # 2. Prove the on-disk storage name IS UUID-prefixed (i.e. the test can
    #    tell the two apart; a regression to file_path.name would fail below).
    stored_files = list(isolated_server["uploads"].glob(f"{document_id}_*"))
    assert len(stored_files) == 1, "expected the UUID-prefixed stored file"
    stored_name = stored_files[0].name
    assert stored_name.startswith(document_id)
    assert stored_name != UPLOAD_NAME

    # 3. Retrieve through the real engine pointed at the isolated store.
    from app.core.config import Settings
    from app.services.knowledge import DBGPTKnowledgeEngine

    settings = Settings(
        database_url=isolated_server["env"]["OPENJM_DATABASE_URL"],
        upload_dir=Path(isolated_server["env"]["OPENJM_UPLOAD_DIR"]),
        vector_path=Path(isolated_server["env"]["OPENJM_VECTOR_PATH"]),
        credential_key_file=Path(isolated_server["env"]["OPENJM_CREDENTIAL_KEY_FILE"]),
    )
    engine = DBGPTKnowledgeEngine()
    engine.settings = settings
    engine.__dict__.pop("embedding_fn", None)

    evidence = await engine.retrieve(
        "How many engineers and data scientists are on the Project Atlas team?",
        [(document_id, UPLOAD_NAME)],
    )
    assert evidence, "expected retrievable evidence for the uploaded document"

    for item in evidence:
        metadata = item.metadata
        # customer-facing names are the ORIGINAL filename
        assert metadata.get("source_name") == UPLOAD_NAME
        assert metadata.get("source") == UPLOAD_NAME
        assert metadata.get("document_id") == document_id
        # no UUID-prefixed storage filename anywhere in metadata values
        # (document_id itself legitimately equals the id — that is the
        # server-owned identity field, not a storage-filename leak)
        for key, value in metadata.items():
            if isinstance(value, str) and key != "document_id":
                assert not value.startswith(document_id), (
                    f"storage filename leaked via {key}: {value!r}"
                )
                assert "/" not in value or value == UPLOAD_NAME, (
                    f"path-like value in {key}: {value!r}"
                )

    # 4. The fact itself is retrievable from the uploaded document.
    joined = " ".join(item.passage for item in evidence)
    assert "12 engineers and 4 data scientists" in joined

    # 5. Cleanup through the real API: deletion must remove the collection.
    deleted = httpx.delete(
        f"{base}/api/knowledge/documents/{document_id}", timeout=60
    )
    assert deleted.status_code == 200
    remaining = httpx.get(f"{base}/api/knowledge/documents", timeout=30).json()
    assert all(doc["id"] != document_id for doc in remaining)


@pytest.mark.asyncio
async def test_direct_ingest_still_defaults_to_file_name(isolated_server):
    """Backward compatibility: direct engine calls without source_name keep
    using file_path.name (benchmark/harness behavior unchanged)."""

    import shutil
    import tempfile

    from app.core.config import Settings
    from app.services.knowledge import DBGPTKnowledgeEngine

    vector = Path(tempfile.mkdtemp(prefix="direct_ingest_"))
    (vector / "chromadb").mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_url=isolated_server["env"]["OPENJM_DATABASE_URL"],
        upload_dir=Path(isolated_server["env"]["OPENJM_UPLOAD_DIR"]),
        vector_path=vector,
        credential_key_file=Path(isolated_server["env"]["OPENJM_CREDENTIAL_KEY_FILE"]),
    )
    engine = DBGPTKnowledgeEngine()
    engine.settings = settings
    engine.__dict__.pop("embedding_fn", None)

    await engine.ingest("direct-ingest-doc", FIXTURE)
    evidence = await engine.retrieve(
        "How many engineers and data scientists are on the Project Atlas team?",
        [("direct-ingest-doc", FIXTURE.name)],
    )
    assert evidence
    assert evidence[0].metadata.get("source_name") == FIXTURE.name
    await engine.delete("direct-ingest-doc")
    shutil.rmtree(vector, ignore_errors=True)
