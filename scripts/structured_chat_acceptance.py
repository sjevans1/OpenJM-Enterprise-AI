"""Runtime Gate H/I acceptance for the Vertical Slice 2 structured Chat path."""

import argparse
from pathlib import Path
import sys

import httpx

from create_demo_database import create_demo_database


def fail(message: str) -> int:
    print("FAIL", message)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--keep-source", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    db_path = create_demo_database(repo_root / "data" / "structured-demo.db")
    connection_uri = f"sqlite:///{db_path.as_posix()}"
    base = args.base_url.rstrip("/")
    client = httpx.Client(timeout=600)

    created = client.post(
        f"{base}/api/data/sources",
        json={
            "name": "OpenJM Structured Demo",
            "engine": "sqlite",
            "connection_uri": connection_uri,
            "enabled": True,
        },
    )
    created.raise_for_status()
    source = created.json()
    source_id = source["id"]
    print("PASS source registered:", source_id)

    tested = client.post(f"{base}/api/data/sources/{source_id}/test")
    tested.raise_for_status()
    if not tested.json().get("ok"):
        return fail(f"source connection failed: {tested.json()}")
    print("PASS source connection")

    refreshed = client.post(f"{base}/api/data/sources/{source_id}/refresh")
    refreshed.raise_for_status()
    if refreshed.json().get("table_count") != 4:
        return fail("acceptance source did not discover four tables")
    print("PASS source schema")

    grounded = client.post(
        f"{base}/api/chat",
        json={
            "message": "What is the total revenue for Blue Mountain Cafe?"
        },
    )
    grounded.raise_for_status()
    body = grounded.json()

    if body.get("execution_class") != "structured":
        return fail(
            "revenue question did not route STRUCTURED: "
            + str(body.get("execution_class"))
        )
    if "325" not in body.get("answer", ""):
        return fail("structured answer did not contain expected revenue 325")
    evidence = body.get("evidence") or []
    if not evidence:
        return fail("structured answer returned no evidence")
    structured_evidence = [
        item
        for item in evidence
        if item.get("source_type") == "structured_query"
        and item.get("source_id") == source_id
    ]
    if not structured_evidence:
        return fail("structured evidence did not identify the registered source")
    metadata = structured_evidence[0].get("metadata") or {}
    if not metadata.get("sql"):
        return fail("structured evidence did not preserve executed SQL")
    if metadata.get("row_count", 0) < 1:
        return fail("structured evidence did not preserve result row count")

    print("PASS STRUCTURED routing")
    print("PASS grounded revenue answer:", body["answer"])
    print("PASS structured evidence + SQL provenance")

    unsupported = client.post(
        f"{base}/api/chat",
        json={
            "message": "What is the total payroll bonus this month?"
        },
    )
    unsupported.raise_for_status()
    unsupported_body = unsupported.json()
    if unsupported_body.get("execution_class") != "structured":
        return fail(
            "unsupported enterprise-data question did not stay in safe STRUCTURED path"
        )
    if unsupported_body.get("evidence"):
        return fail("unsupported schema question returned fabricated evidence")
    answer = unsupported_body.get("answer", "").lower()
    if (
        "no database query was executed" not in answer
        and "could not safely execute" not in answer
    ):
        return fail(
            "unsupported schema question did not return a safe no-execution answer"
        )
    print("PASS unsupported schema fails closed with no evidence")

    if not args.keep_source:
        deleted = client.delete(f"{base}/api/data/sources/{source_id}")
        deleted.raise_for_status()
        print("PASS acceptance source deleted")
    else:
        print("PASS acceptance source retained")

    print("STRUCTURED CHAT ACCEPTANCE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
