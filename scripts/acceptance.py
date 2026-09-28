"""Runtime acceptance checks for a running OpenJM Vertical Slice 1."""

import argparse
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--document")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    client = httpx.Client(timeout=180)

    health = client.get(f"{base}/api/health")
    health.raise_for_status()
    print("PASS health:", health.json())

    first = client.post(
        f"{base}/api/chat",
        json={"message": "Hello, my name is Sam."},
    )
    first.raise_for_status()
    first_body = first.json()
    conversation_id = first_body["conversation_id"]
    print("PASS conversation created:", conversation_id)

    second = client.post(
        f"{base}/api/chat",
        json={
            "message": "What is my name?",
            "conversation_id": conversation_id,
        },
    )
    second.raise_for_status()
    answer = second.json()["answer"]
    if "sam" not in answer.lower():
        print("FAIL model did not recall Sam:", answer)
        return 1
    print("PASS server-side conversation memory:", answer)

    detail = client.get(f"{base}/api/conversations/{conversation_id}")
    detail.raise_for_status()
    messages = detail.json()["messages"]
    if len(messages) < 4:
        print("FAIL persisted history has fewer than four messages")
        return 1
    print("PASS persisted history:", len(messages), "messages")

    if args.document:
        with open(args.document, "rb") as handle:
            upload = client.post(
                f"{base}/api/knowledge/documents",
                files={"file": (args.document, handle)},
            )
        upload.raise_for_status()
        doc = upload.json()
        print("PASS document indexed:", doc["original_name"])

        catalog = client.post(
            f"{base}/api/chat",
            json={
                "message": "What documents do you have loaded?",
                "conversation_id": conversation_id,
            },
        )
        catalog.raise_for_status()
        catalog_body = catalog.json()
        if doc["original_name"].lower() not in catalog_body["answer"].lower():
            print("FAIL catalog answer did not include uploaded document")
            return 1
        if not catalog_body["evidence"]:
            print("FAIL catalog returned no evidence")
            return 1
        print("PASS document catalog:", catalog_body["answer"])

    print("ALL REQUESTED CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
