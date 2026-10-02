"""Real VS3-C3 hardening acceptance against an isolated local instance.

This gate uses the real API, DB-GPT/Chroma ingestion, local model, governed
SQLite planner/query path, and execution-trace database. It refuses nonempty
catalogs and deletes only resources created by this run.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from typing import Any

import httpx


QUESTION = (
    "According to our revenue policy, which customers exceed the FY2025 annual "
    "revenue threshold in the policy, based on customer_revenue records?"
)
POLICY = "# FY2025 Revenue Policy\n\nFY2025 annual revenue exceeds USD 300.\n"
POLICY_500 = "# FY2025 Revenue Policy\n\nFY2025 annual revenue exceeds USD 500.\n"
CONFLICTING_POLICY = (
    "# Conflicting FY2025 Revenue Policy\n\n"
    "FY2025 annual revenue exceeds USD 300.\n"
    "FY2025 annual revenue exceeds USD 500.\n"
)


class AcceptanceFailure(Exception):
    pass


def check(condition: bool, explanation: str) -> None:
    if not condition:
        raise AcceptanceFailure(explanation)
    print("PASS", explanation)


def call(client: httpx.Client, method: str, url: str, **kwargs) -> Any:
    response = client.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json()


def setup_fixture(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE customer_revenue (
                customer TEXT PRIMARY KEY,
                fy2025_annual_revenue REAL NOT NULL
            );
            INSERT INTO customer_revenue VALUES ('Blue Mountain Cafe', 325.0);
            INSERT INTO customer_revenue VALUES ('Kingston Market', 410.0);
            INSERT INTO customer_revenue VALUES ('Montego Retail', 525.0);
            INSERT INTO customer_revenue VALUES ('Portland Foods', 301.0);
            INSERT INTO customer_revenue VALUES ('Island Retail', 300.0);
            INSERT INTO customer_revenue VALUES ('Harbour Shop', 200.0);
            """
        )


def trace_count(app_db: Path) -> int:
    with sqlite3.connect(app_db) as db:
        return int(
            db.execute(
                "SELECT COUNT(*) FROM execution_traces "
                "WHERE tool_name = 'structured.query'"
            ).fetchone()[0]
        )


def upload_policy(client: httpx.Client, base: str, name: str, text: str) -> str:
    body = call(
        client,
        "POST",
        base + "/api/knowledge/documents",
        files={"file": (name, text.encode("utf-8"), "text/markdown")},
    )
    check(body.get("indexed") is True, f"{name}: real Knowledge indexing succeeded")
    return str(body["id"])


def delete_owned(client: httpx.Client, base: str, path: str, identity: str) -> None:
    response = client.delete(base + path + identity)
    if response.status_code not in (200, 404):
        raise AcceptanceFailure(
            f"Cleanup failed for owned fixture {identity}: HTTP {response.status_code}"
        )
    catalog_path = (
        "/api/knowledge/documents"
        if path.startswith("/api/knowledge/")
        else "/api/data/sources"
    )
    catalog = call(client, "GET", base + catalog_path)
    if any(str(item.get("id")) == identity for item in catalog):
        raise AcceptanceFailure(f"Owned fixture still present after cleanup: {identity}")


def assert_no_sql(
    client: httpx.Client,
    base: str,
    app_db: Path,
    label: str,
) -> dict:
    before = trace_count(app_db)
    body = call(
        client,
        "POST",
        base + "/api/chat",
        json={"message": QUESTION, "mode": "hybrid"},
    )
    after = trace_count(app_db)
    check(body.get("execution_class") == "hybrid", f"{label}: HYBRID preserved")
    check(after == before, f"{label}: zero structured.query executions")
    check(
        not any(
            item.get("source_type") == "structured_query"
            for item in body.get("evidence", [])
        ),
        f"{label}: no structured result evidence",
    )
    return body


def verify_positive(body: dict, document_id: str, source_id: str) -> None:
    check(body.get("execution_class") == "hybrid", "positive: HYBRID class")
    check(body.get("mode") == "hybrid", "positive: HYBRID mode")
    evidence = body.get("evidence", [])
    documents = [
        item
        for item in evidence
        if item.get("source_type") == "document"
        and item.get("source_id") == document_id
    ]
    data = [
        item
        for item in evidence
        if item.get("source_type") == "structured_query"
        and item.get("source_id") == source_id
    ]
    check(bool(documents), "positive: policy evidence present")
    check(len(data) == 1, "positive: one structured result")
    provenance = data[0].get("provenance") or {}
    grounded = provenance.get("grounded_parameter") or {}
    sql = str(provenance.get("executed_sql") or data[0].get("metadata", {}).get("sql") or "")
    check("fy2025_annual_revenue > 300" in sql.lower(), "positive: exact grounded predicate")
    check(grounded.get("value") == "300", "positive: threshold value provenance")
    check(grounded.get("operator") == ">", "positive: operator provenance")
    check(grounded.get("currency") == "USD", "positive: currency provenance")
    check(grounded.get("source_id") == document_id, "positive: policy source provenance")
    passage = str(data[0].get("passage", "")).lower()
    for customer in (
        "blue mountain cafe",
        "kingston market",
        "montego retail",
        "portland foods",
    ):
        check(customer in passage, f"positive: result includes {customer}")
    check("island retail" not in passage, "positive: boundary value 300 excluded")
    check("harbour shop" not in passage, "positive: below-threshold value excluded")
    answer = str(body.get("answer", ""))
    check("[DOC " in answer and "[DATA " in answer, "positive: both citation types")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--isolated-instance",
        action="store_true",
        required=True,
        help="Confirm the running app uses disposable, empty acceptance catalogs.",
    )
    parser.add_argument(
        "--app-db",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "openjm.db",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    owned_documents: list[str] = []
    source_id = ""

    with TemporaryDirectory(prefix="openjm-c3-") as directory, httpx.Client(
        timeout=600
    ) as client:
        try:
            health = call(client, "GET", base + "/api/health")
            check(health.get("status") == "ok", "API healthy")
            check(call(client, "GET", base + "/api/knowledge/documents") == [], "document catalog empty")
            check(call(client, "GET", base + "/api/data/sources") == [], "source catalog empty")

            fixture = Path(directory) / "c3_revenue.sqlite"
            setup_fixture(fixture)
            source = call(
                client,
                "POST",
                base + "/api/data/sources",
                json={
                    "name": "C3 Isolated FY2025 Revenue",
                    "engine": "sqlite",
                    "connection_uri": "sqlite:///" + fixture.as_posix(),
                    "revenue_currency": "USD",
                    "enabled": True,
                },
            )
            source_id = str(source["id"])
            check(source.get("revenue_currency") == "USD", "source currency attested")
            connection = call(
                client, "POST", base + f"/api/data/sources/{source_id}/test"
            )
            check(connection.get("ok") is True, "SQLite connection verified")
            schema = call(
                client, "POST", base + f"/api/data/sources/{source_id}/refresh"
            )
            check(schema.get("table_count") == 1, "authorized schema discovered")

            policy_id = upload_policy(
                client, base, "fy2025_revenue_policy.md", POLICY
            )
            owned_documents.append(policy_id)
            before = trace_count(args.app_db)
            positive = call(
                client,
                "POST",
                base + "/api/chat",
                json={"message": QUESTION, "mode": "hybrid"},
            )
            after = trace_count(args.app_db)
            if after != before + 1:
                raise AcceptanceFailure(
                    "positive: exactly one structured query; "
                    f"trace delta={after - before}, "
                    f"answer={positive.get('answer', '')!r}"
                )
            check(True, "positive: exactly one structured query")
            verify_positive(positive, policy_id, source_id)

            delete_owned(
                client, base, "/api/knowledge/documents/", owned_documents.pop()
            )
            assert_no_sql(client, base, args.app_db, "missing policy")

            policy_id = upload_policy(
                client, base, "fy2025_revenue_policy_500.md", POLICY_500
            )
            owned_documents.append(policy_id)
            before = trace_count(args.app_db)
            mutated = call(
                client,
                "POST",
                base + "/api/chat",
                json={"message": QUESTION, "mode": "hybrid"},
            )
            check(trace_count(args.app_db) == before + 1, "mutation: re-queried with new policy")
            sqls = []
            with sqlite3.connect(args.app_db) as db:
                for (sql,) in db.execute(
                    "SELECT executed_sql FROM execution_traces "
                    "WHERE tool_name='structured.query' AND status='succeeded' "
                    "ORDER BY created_at DESC LIMIT 1"
                ):
                    sqls.append(sql)
            check(
                any(sql and "500" in sql for sql in sqls),
                "mutation: SQL predicate reflects policy value 500",
            )
            answer = str(mutated.get("answer", ""))
            check("Portland Foods" not in answer, "mutation: 500 threshold shifts boundary")
            mutated_data = [
                item
                for item in mutated.get("evidence", [])
                if item.get("source_type") == "structured_query"
            ]
            check(len(mutated_data) == 1, "mutation: one structured result")
            mutated_payload = json.loads(mutated_data[0].get("passage", "{}"))
            check(
                mutated_payload.get("rows") == [["Montego Retail"]],
                "mutation: structured rows exactly reflect the 500 threshold",
            )
            delete_owned(
                client, base, "/api/knowledge/documents/", owned_documents.pop()
            )

            conflict_id = upload_policy(
                client,
                base,
                "conflicting_fy2025_revenue_policy.md",
                CONFLICTING_POLICY,
            )
            owned_documents.append(conflict_id)
            assert_no_sql(client, base, args.app_db, "conflicting policy")
            delete_owned(
                client, base, "/api/knowledge/documents/", owned_documents.pop()
            )

            policy_id = upload_policy(
                client, base, "fy2025_revenue_policy.md", POLICY
            )
            owned_documents.append(policy_id)
            call(
                client,
                "PATCH",
                base + f"/api/data/sources/{source_id}/currency",
                json={"revenue_currency": "JMD"},
            )
            assert_no_sql(client, base, args.app_db, "currency mismatch")
        except (AcceptanceFailure, httpx.HTTPError, KeyError, sqlite3.Error) as exc:
            print("FAIL", exc)
            return 1
        finally:
            cleanup_errors: list[str] = []
            for document_id in owned_documents:
                try:
                    delete_owned(
                        client, base, "/api/knowledge/documents/", document_id
                    )
                except (AcceptanceFailure, httpx.HTTPError) as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            if source_id:
                try:
                    delete_owned(client, base, "/api/data/sources/", source_id)
                except (AcceptanceFailure, httpx.HTTPError) as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            if cleanup_errors:
                print("FAIL cleanup", "; ".join(cleanup_errors))

        if cleanup_errors:
            return 1
        check(
            call(client, "GET", base + "/api/knowledge/documents") == [],
            "cleanup: document catalog empty",
        )
        check(
            call(client, "GET", base + "/api/data/sources") == [],
            "cleanup: source catalog empty",
        )
        check(True, "policy value mutation proof: document value drives query")
    print("VS3-C3 LIVE HARDENING ACCEPTANCE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
