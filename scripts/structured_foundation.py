"""Runtime smoke test for Vertical Slice 2 source registration and schema discovery."""

import argparse
from pathlib import Path
import sys

import httpx

from create_demo_database import create_demo_database


def fail(message: str) -> int:
    print("FAIL", message)
    return 1


def assert_no_secret(payload: object) -> bool:
    text = str(payload).lower()
    return (
        "connection_uri" not in text
        and "connection_secret" not in text
        and "structured-demo.db?" not in text
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--keep-source", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    db_path = create_demo_database(repo_root / "data" / "structured-demo.db")
    connection_uri = f"sqlite:///{db_path.as_posix()}"

    client = httpx.Client(timeout=60)
    base = args.base_url.rstrip("/")

    health = client.get(f"{base}/api/health")
    health.raise_for_status()
    print("PASS health")

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
    if not assert_no_secret(source):
        return fail("source creation response exposed connection credentials")
    print("PASS source registration:", source_id)

    tested = client.post(f"{base}/api/data/sources/{source_id}/test")
    tested.raise_for_status()
    test_body = tested.json()
    if not test_body.get("ok"):
        return fail(f"connection test failed: {test_body}")
    print("PASS connection test")

    refreshed = client.post(f"{base}/api/data/sources/{source_id}/refresh")
    refreshed.raise_for_status()
    refresh_body = refreshed.json()
    names = {table["name"] for table in refresh_body["source"]["tables"]}
    expected = {"customers", "products", "orders", "order_items"}
    if names != expected:
        return fail(f"unexpected schema inventory: {sorted(names)}")
    if not assert_no_secret(refresh_body):
        return fail("schema refresh response exposed connection credentials")
    print("PASS schema discovery:", ", ".join(sorted(names)))

    fetched = client.get(f"{base}/api/data/sources/{source_id}")
    fetched.raise_for_status()
    if not assert_no_secret(fetched.json()):
        return fail("source GET exposed connection credentials")
    print("PASS credential redaction contract")

    disabled = client.patch(
        f"{base}/api/data/sources/{source_id}",
        json={"enabled": False},
    )
    disabled.raise_for_status()
    if disabled.json().get("enabled") is not False:
        return fail("source disable did not persist")
    print("PASS source disable")

    if args.keep_source:
        client.patch(
            f"{base}/api/data/sources/{source_id}",
            json={"enabled": True},
        ).raise_for_status()
        print("PASS source retained and re-enabled for structured chat testing")
    else:
        deleted = client.delete(f"{base}/api/data/sources/{source_id}")
        deleted.raise_for_status()
        remaining = client.get(f"{base}/api/data/sources")
        remaining.raise_for_status()
        if any(item["id"] == source_id for item in remaining.json()):
            return fail("deleted source still appears in source catalog")
        print("PASS source deletion")

    print("STRUCTURED FOUNDATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
