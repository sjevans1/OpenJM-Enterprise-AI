"""BV6-B candidate <-> reports.py compatibility.

A BV6-B candidate is an Evidence with ``source_type == 'report'``. These tests
prove the reports surfaces recognise it and defer its revalidation to the
provenance-declared pins, so a message that contains a candidate can still be
saved as a governed snapshot (no regression) and revalidates correctly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.reports import (
    _parse_evidence,
    _source_ids,
    _sources_available,
    _structured_tables,
)
from app.core.identity import Principal
from app.core.permissions import permissions_for_role
from app.models import DataSource, Document, Tenant

TENANT = "tnt-bv6b-int"
OWNER = "bv6b-int-owner"


def _principal(principal_id=OWNER, tenant_id=TENANT) -> Principal:
    return Principal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        subject=f"sub:{principal_id}",
        role="owner",
        membership_id=f"m:{tenant_id}:{principal_id}",
        auth_method="local-dev",
        permissions=permissions_for_role("owner"),
    )


def _doc(db, *, name):
    document = Document(
        tenant_id=TENANT,
        user_id=OWNER,
        original_name=name,
        stored_path=f"/tmp/{name}",
        size_bytes=10,
        status="ready",
        indexed=True,
        classification="internal",
        tenant_visible=True,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(document)
    return document


def _candidate_ref(*, report_id, documents, tables):
    return {
        "source_type": "report",
        "source_id": report_id,
        "title": "Canonical launch procedure",
        "passage": "Governed authoritative report reference.",
        "provenance": {
            "candidate_kind": "authoritative_report",
            "pinned_document_ids": list(documents),
            "pinned_source_tables": tables,
        },
    }


async def _source(db, *, name, tables):
    source = DataSource(
        tenant_id=TENANT,
        user_id=OWNER,
        name=name,
        engine="postgresql",
        connection_secret="enc-secret",
        status="connected",
        enabled=True,
        schema_json=json.dumps([{"name": t} for t in tables]),
        authorized_objects_json=json.dumps(tables),
        classification="internal",
        tenant_visible=True,
    )
    db.add(source)
    return source


def test_parse_evidence_accepts_a_well_formed_candidate_reference():
    raw = json.dumps([_candidate_ref(report_id="r1", documents=["d1"], tables={})])
    evidence = _parse_evidence(raw)
    assert evidence[0].source_type == "report"


@pytest.mark.parametrize(
    "provenance",
    [
        {},  # no pins at all
        {"pinned_document_ids": "d1", "pinned_source_tables": {}},  # not a list
        {"pinned_document_ids": [1], "pinned_source_tables": {}},  # non-str id
        {"pinned_document_ids": [], "pinned_source_tables": "x"},  # not a dict
        {"pinned_document_ids": [], "pinned_source_tables": {"s": []}},  # empty tables
    ],
)
def test_parse_evidence_rejects_malformed_candidate_provenance(provenance):
    raw = json.dumps(
        [
            {
                "source_type": "report",
                "source_id": "r1",
                "title": "t",
                "passage": "p",
                "provenance": provenance,
            }
        ]
    )
    with pytest.raises(HTTPException) as exc:
        _parse_evidence(raw)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_candidate_pins_are_surfaced_for_revalidation(file_db):
    async with file_db() as db:
        db.add(Tenant(id=TENANT, slug="int", name="Int", status="active"))
        doc = _doc(db, name="alpha.txt")
        source = await _source(db, name="warehouse", tables=["finance"])
        await db.flush()
        evidence = _parse_evidence(
            json.dumps(
                [
                    _candidate_ref(
                        report_id="r1",
                        documents=[doc.id, "missing-doc"],
                        tables={source.id: ["finance"]},
                    )
                ]
            )
        )
        documents, sources = _source_ids(evidence)
        tables = _structured_tables(evidence)

    assert doc.id in documents
    assert source.id in sources
    assert tables == {source.id: {"finance"}}


@pytest.mark.asyncio
async def test_sources_available_passes_then_fails_on_revocation(file_db):
    async with file_db() as db:
        db.add(Tenant(id=TENANT, slug="int", name="Int", status="active"))
        doc = _doc(db, name="alpha.txt")
        await db.flush()
        evidence = _parse_evidence(
            json.dumps([_candidate_ref(report_id="r1", documents=[doc.id], tables={})])
        )
        assert await _sources_available(db, evidence, principal=_principal()) is True
        # Revoke the pinned source and the candidate reference fails closed.
        doc.indexed = False
        await db.flush()
        assert await _sources_available(db, evidence, principal=_principal()) is False
