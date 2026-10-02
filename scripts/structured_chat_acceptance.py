"""Runtime Gate H/I acceptance for the Vertical Slice 2 structured Chat path."""

import argparse
from pathlib import Path
import sys

import httpx

from create_demo_database import create_demo_database


def fail(message: str) -> int:
    print("FAIL", message)
    return 1


def source_catalog(client: httpx.Client, base: str) -> list[dict]:
    response = client.get(f"{base}/api/data/sources")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("data source catalog response was not a list")
    return payload


def require_isolated_source_catalog(client: httpx.Client, base: str) -> bool:
    existing = source_catalog(client, base)
    if not existing:
        return True

    visible = ", ".join(
        f"{item.get('name', '<unnamed>')} ({item.get('id', '<unknown>')})"
        for item in existing
    )
    print(
        "FAIL structured chat acceptance requires an isolated data-source "
        f"catalog; found pre-existing sources: {visible}"
    )
    return False


def cleanup_source(
    client: httpx.Client,
    base: str,
    source_id: str,
    *,
    verify: bool,
) -> bool:
    try:
        deleted = client.delete(f"{base}/api/data/sources/{source_id}")
        deleted.raise_for_status()
        if verify:
            remaining = source_catalog(client, base)
            if any(item.get("id") == source_id for item in remaining):
                print("FAIL acceptance source still appears after cleanup")
                return False
        return True
    except Exception as exc:
        print(f"WARN acceptance source cleanup failed: {exc.__class__.__name__}")
        return False


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

    if not require_isolated_source_catalog(client, base):
        return 1

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

    try:
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
                "message": "What is the total revenue for Blue Mountain Cafe?",
                "mode": "data",
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

        disabled = client.patch(
            f"{base}/api/data/sources/{source_id}",
            json={"enabled": False},
        )
        disabled.raise_for_status()
        if disabled.json().get("enabled") is not False:
            return fail("source did not disable")

        while_disabled = client.post(
            f"{base}/api/chat",
            json={
                "message": "What is the total revenue for Blue Mountain Cafe?",
                "mode": "data",
            },
        )
        while_disabled.raise_for_status()
        disabled_body = while_disabled.json()
        if disabled_body.get("execution_class") != "structured":
            return fail(
                "structured enterprise-data request fell through to GENERAL while source disabled"
            )
        if disabled_body.get("evidence"):
            return fail("disabled source returned structured evidence")
        disabled_answer = disabled_body.get("answer", "").lower()
        if "no database query was executed" not in disabled_answer:
            return fail("disabled source did not fail closed with no-execution answer")
        print("PASS disabled source remains STRUCTURED and fails closed")

        reenabled = client.patch(
            f"{base}/api/data/sources/{source_id}",
            json={"enabled": True},
        )
        reenabled.raise_for_status()
        if reenabled.json().get("enabled") is not True:
            return fail("source did not re-enable")
        print("PASS source re-enabled")

        unsupported = client.post(
            f"{base}/api/chat",
            json={
                "message": "What is the total payroll bonus this month?",
                "mode": "data",
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
            if not cleanup_source(client, base, source_id, verify=True):
                return fail("acceptance source cleanup failed")
            source_id = ""
            print("PASS acceptance source deleted")
        else:
            print("PASS acceptance source retained")

        print("STRUCTURED CHAT ACCEPTANCE PASSED")
        return 0
    finally:
        if source_id and not args.keep_source:
            cleanup_source(client, base, source_id, verify=False)


if __name__ == "__main__":
    sys.exit(main())
