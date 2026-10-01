"""Deterministic tests for scripts/acceptance.py isolation helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "acceptance.py"
SPEC = importlib.util.spec_from_file_location("openjm_acceptance_script", SCRIPT)
assert SPEC and SPEC.loader
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


class FakeResponse:
    def __init__(self, payload=None, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "fake error", request=request, response=response
            )


class FakeClient:
    def __init__(self, documents: list[dict]):
        self.documents = [dict(item) for item in documents]
        self.deleted_ids: list[str] = []

    def get(self, url: str):
        assert url.endswith("/api/knowledge/documents")
        return FakeResponse([dict(item) for item in self.documents])

    def delete(self, url: str):
        document_id = url.rsplit("/", 1)[-1]
        self.deleted_ids.append(document_id)
        before = len(self.documents)
        self.documents = [
            item for item in self.documents if item.get("id") != document_id
        ]
        if len(self.documents) == before:
            return FakeResponse({"detail": "not found"}, status_code=404)
        return FakeResponse({"deleted": True, "document_id": document_id})


def test_isolated_catalog_passes_when_empty():
    client = FakeClient([])
    assert acceptance.require_isolated_knowledge_catalog(
        client, "http://test"
    )


def test_isolated_catalog_fails_without_mutating_existing_documents():
    existing = [{"id": "user-doc", "original_name": "customer-report.pdf"}]
    client = FakeClient(existing)

    assert not acceptance.require_isolated_knowledge_catalog(
        client, "http://test"
    )
    assert client.documents == existing
    assert client.deleted_ids == []


def test_cleanup_deletes_only_acceptance_owned_document():
    client = FakeClient(
        [
            {"id": "acceptance-doc", "original_name": "phoenix.md"},
            {"id": "user-doc", "original_name": "customer-report.pdf"},
        ]
    )

    assert acceptance.cleanup_owned_document(
        client,
        "http://test",
        "acceptance-doc",
        verify=True,
    )
    assert client.deleted_ids == ["acceptance-doc"]
    assert client.documents == [
        {"id": "user-doc", "original_name": "customer-report.pdf"}
    ]


def test_cleanup_is_idempotent_when_document_is_already_gone():
    client = FakeClient([])

    assert acceptance.cleanup_owned_document(
        client,
        "http://test",
        "acceptance-doc",
        verify=True,
    )
    assert client.deleted_ids == ["acceptance-doc"]
