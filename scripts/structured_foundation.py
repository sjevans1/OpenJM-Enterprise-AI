"""Runtime smoke test for Vertical Slice 2 source registration and schema discovery."""

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
        "FAIL structured foundation requires an isolated data-source "
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
                print("FAIL foundation source still appears after cleanup")
                return False
        return True
    except Exception as exc:
        print(f"WARN foundation source cleanup failed: {exc.__class__.__name__}")
        return False


def assert_no_secret(payload: object, connection_uri: str) -> bool:
    text = str(payload)
    lowered = text.lower()
    return (
        "connection_uri" not in lowered
        and "connection_secret" not in lowered
        and connection_uri not in text
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
    if not assert_no_secret(source, connection_uri):
        cleanup_source(client, base, source_id, verify=False)
        return fail("source creation response exposed connection credentials")
    print("PASS source registration:", source_id)

    try:
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
        if not assert_no_secret(refresh_body, connection_uri):
            return fail("schema refresh response exposed connection credentials")
        print("PASS schema discovery:", ", ".join(sorted(names)))

        fetched = client.get(f"{base}/api/data/sources/{source_id}")
        fetched.raise_for_status()
        if not assert_no_secret(fetched.json(), connection_uri):
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
            print("PASS source retained and re-enabled")
        else:
            if not cleanup_source(client, base, source_id, verify=True):
                return fail("source cleanup failed")
            source_id = ""
            print("PASS source deletion")

        print("STRUCTURED FOUNDATION CHECKS PASSED")
        return 0
    finally:
        if source_id and not args.keep_source:
            cleanup_source(client, base, source_id, verify=False)


if __name__ == "__main__":
    sys.exit(main())
