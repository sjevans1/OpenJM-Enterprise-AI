"""Live VS3-C3 acceptance against an ISOLATED local OpenJM instance only.

Requires the local model, real Knowledge ingestion/vector runtime, and the API
to be running. Does not stub tools, SQL execution, Knowledge or the model.
Creates temporary synthetic SQLite rows, one app data-source registration and
one test Knowledge document. It refuses nonempty source/document catalogs and
cleans up only its own artifacts, including on assertion failure.

Usage:
    python scripts/dependent_hybrid_acceptance.py --isolated-instance
"""
import argparse
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

import httpx


QUESTION = (
    "According to our revenue policy, which customers exceed the annual "
    "revenue threshold in the policy, based on the authorized "
    "customer_revenue annual revenue records?"
)
POLICY = (
    "# Customer Revenue Eligibility Policy\n\n"
    "Annual revenue threshold: USD 300\n"
    "Customers with annual revenue exceeding this threshold are eligible.\n"
    "The policy does not authorize changing currency, units or measurement period.\n"
)


class AcceptanceFailure(Exception):
    pass


def check(condition: bool, explanation: str) -> None:
    if not condition:
        raise AcceptanceFailure(explanation)
    print("PASS", explanation)


def call(client: httpx.Client, method: str, url: str, **kwargs) -> dict | list:
    response = client.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json()


def setup_fixture(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE customer_revenue (
                customer TEXT PRIMARY KEY,
                annual_revenue REAL NOT NULL
            );
            INSERT INTO customer_revenue VALUES ('Blue Mountain Cafe', 325.0);
            INSERT INTO customer_revenue VALUES ('Island Retail', 200.0);
            """
        )


def verify_evidence(body: dict, doc_id: str, source_id: str) -> None:
    check(body.get("execution_class") == "hybrid", "HYBRID class preserved")
    check(body.get("mode") == "hybrid", "HYBRID mode preserved")
    evidence = body.get("evidence", [])
    documents = [
        ev for ev in evidence
        if ev.get("source_type") == "document" and ev.get("source_id") == doc_id
    ]
    data = [
        ev for ev in evidence
        if ev.get("source_type") == "structured_query"
        and ev.get("source_id") == source_id
    ]
    check(bool(documents), "Authorized policy document evidence present")
    check(len(data) == 1, "Exactly one authorized structured result")
    sql = str(data[0].get("metadata", {}).get("sql", "")).lower()
    check("annual_revenue > 300" in sql, "Verified threshold applied to annual_revenue")
    check("customer_revenue" in sql, "Query stays in authorized customer revenue table")
    check("drop " not in sql and "update " not in sql, "No mutation SQL")
    provenance = data[0].get("provenance") or {}
    check(
        provenance.get("policy_threshold_source_id") == doc_id,
        "Database result is linked to policy source id",
    )
    check(
        provenance.get("policy_threshold_currency") == "USD",
        "Policy threshold currency is USD",
    )
    check(
        provenance.get("structured_source_currency") == "USD",
        "Selected database source currency is USD",
    )
    check(
        "blue mountain cafe" in str(body.get("answer", "")).lower(),
        "Assistant answer identifies the supported eligible customer",
    )
    answer = body.get("answer", "")
    check("[DOC " in answer and "[DATA " in answer, "Answer cites both evidence types")


def assert_fail_closed(
    client: httpx.Client, base: str, doc_id: str, source_id: str, label: str,
) -> None:
    body = call(
        client, "POST", base + "/api/chat",
        json={"message": QUESTION, "mode": "hybrid"},
    )
    check(body.get("execution_class") == "hybrid", label + ": stays HYBRID")
    data = [
        ev for ev in body.get("evidence", [])
        if ev.get("source_type") == "structured_query"
    ]
    check(not data, label + ": no data result was asserted")
    check(
        "blue mountain cafe" not in str(body.get("answer", "")).lower(),
        label + ": no customer qualification asserted",
    )


def cleanup(client: httpx.Client, base: str, path: str, identity: str) -> None:
    response = client.delete(base + path + identity)
    if response.status_code not in (200, 404):
        print("WARN cleanup failed for own fixture:", response.status_code)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--isolated-instance", action="store_true", required=True,
        help="Confirm this is a disposable local app database (test leaves chat history).",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    source_id = ""
    document_id = ""
    with TemporaryDirectory(prefix="openjm-c3-") as directory, httpx.Client(timeout=600) as client:
        try:
            health = call(client, "GET", base + "/api/health")
            check(health.get("status") == "ok", "OpenJM API is healthy")
            docs = call(client, "GET", base + "/api/knowledge/documents")
            sources = call(client, "GET", base + "/api/data/sources")
            check(isinstance(docs, list) and not docs, "Document catalog isolated")
            check(isinstance(sources, list) and not sources, "Data source catalog isolated")

            fixture = Path(directory) / "c3_revenue.sqlite"
            setup_fixture(fixture)

            created = call(client, "POST", base + "/api/data/sources", json={
                "name": "C3 Isolated Acceptance Revenue",
                "engine": "sqlite",
                "connection_uri": "sqlite:///" + fixture.as_posix(),
                "revenue_currency": "USD",
                "enabled": True,
            })
            source_id = created["id"]
            check(created.get("revenue_currency") == "USD", "Currency attested")
            test = call(client, "POST", base + "/api/data/sources/" + source_id + "/test")
            check(test.get("ok") is True, "SQLite source connection checked")
            schema = call(client, "POST", base + "/api/data/sources/" + source_id + "/refresh")
            check(schema.get("table_count") == 1, "Only revenue table discovered")

            uploaded = call(
                client, "POST", base + "/api/knowledge/documents",
                files={"file": ("customer_policy_c3.md", POLICY.encode("utf8"), "text/markdown")},
            )
            document_id = uploaded["id"]
            check(uploaded.get("indexed") is True, "Real Knowledge indexing succeeded")

            first = call(
                client, "POST", base + "/api/chat",
                json={"message": QUESTION, "mode": "hybrid"},
            )
            verify_evidence(first, document_id, source_id)

            call(
                client, "PATCH", base + "/api/data/sources/" + source_id,
                json={"enabled": False},
            )
            assert_fail_closed(client, base, document_id, source_id, "Disabled source")
            call(
                client, "PATCH", base + "/api/data/sources/" + source_id,
                json={"enabled": True},
            )
            call(
                client, "PATCH", base + "/api/data/sources/" + source_id + "/currency",
                json={"revenue_currency": "JMD"},
            )
            assert_fail_closed(client, base, document_id, source_id, "Currency mismatch")
            call(
                client, "PATCH", base + "/api/data/sources/" + source_id + "/currency",
                json={"revenue_currency": "USD"},
            )
            cleanup(client, base, "/api/knowledge/documents/", document_id)
            document_id = ""
            assert_fail_closed(client, base, document_id, source_id, "Deleted policy")
            print("C3 LIVE ACCEPTANCE PASSED")
            return 0
        except (AcceptanceFailure, httpx.HTTPError, KeyError) as exc:
            print("FAIL", exc)
            return 1
        finally:
            # Never touch documents or sources that were not created by this run.
            if document_id:
                cleanup(client, base, "/api/knowledge/documents/", document_id)
            if source_id:
                cleanup(client, base, "/api/data/sources/", source_id)


if __name__ == "__main__":
    sys.exit(main())
