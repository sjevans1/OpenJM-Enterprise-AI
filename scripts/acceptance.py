"""Runtime acceptance checks for a running OpenJM Vertical Slice 1."""

import argparse
import sys
from pathlib import Path

import httpx


def fail(message: str) -> int:
    print("FAIL", message)
    return 1


def knowledge_catalog(client: httpx.Client, base: str) -> list[dict]:
    response = client.get(f"{base}/api/knowledge/documents")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("knowledge catalog response was not a list")
    return payload


def require_isolated_knowledge_catalog(client: httpx.Client, base: str) -> bool:
    """Require a clean Knowledge catalog for document-backed acceptance.

    Formal Gate C must be reproducible. Pre-existing authorized documents can
    change retrieval results and the model prompt, especially when they contain
    overlapping evidence. The harness therefore fails fast rather than deleting
    or mutating any pre-existing user document.
    """

    existing = knowledge_catalog(client, base)
    if not existing:
        return True

    visible = ", ".join(
        f"{item.get('original_name', '<unnamed>')} ({item.get('id', '<unknown>')})"
        for item in existing
    )
    print(
        "FAIL document-backed acceptance requires an isolated Knowledge "
        f"catalog; found pre-existing documents: {visible}"
    )
    return False


def cleanup_owned_document(
    client: httpx.Client,
    base: str,
    document_id: str,
    *,
    verify: bool,
) -> bool:
    """Delete only the document created by this acceptance run."""

    try:
        deleted = client.delete(f"{base}/api/knowledge/documents/{document_id}")
        if deleted.status_code == 404:
            return True
        deleted.raise_for_status()

        if verify:
            remaining = knowledge_catalog(client, base)
            if any(item.get("id") == document_id for item in remaining):
                print("FAIL acceptance document still appears after cleanup")
                return False
        return True
    except Exception as exc:  # noqa: BLE001 - cleanup must not hide root failure
        print(f"WARN acceptance document cleanup failed: {exc.__class__.__name__}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--document")
    parser.add_argument(
        "--question",
        help="Document-grounded question to use for Gate C.",
    )
    parser.add_argument(
        "--expect",
        help="Case-insensitive substring that must appear in the Gate C answer.",
    )
    parser.add_argument(
        "--delete-after-test",
        action="store_true",
        help=(
            "Delete the document created by this run and verify it is no longer "
            "used as evidence. Cleanup is also attempted from finally if the "
            "run fails after upload."
        ),
    )
    args = parser.parse_args()

    if (args.question or args.expect or args.delete_after_test) and not args.document:
        parser.error("--question/--expect/--delete-after-test require --document")
    if bool(args.question) != bool(args.expect):
        parser.error("--question and --expect must be supplied together")

    base = args.base_url.rstrip("/")
    client = httpx.Client(timeout=300)

    health = client.get(f"{base}/api/health")
    health.raise_for_status()
    print("PASS health:", health.json())

    # Document-backed acceptance is an isolated-system check. Never delete or
    # mutate pre-existing user documents to make the harness pass.
    if args.document and not require_isolated_knowledge_catalog(client, base):
        return 1
    if args.document:
        print("PASS isolated Knowledge catalog")

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
        return fail(f"model did not recall Sam: {answer}")
    print("PASS server-side conversation memory:", answer)

    detail = client.get(f"{base}/api/conversations/{conversation_id}")
    detail.raise_for_status()
    messages = detail.json()["messages"]
    if len(messages) < 4:
        return fail("persisted history has fewer than four messages")
    print("PASS persisted history:", len(messages), "messages")

    uploaded_doc: dict | None = None

    try:
        if args.document:
            document_path = Path(args.document)
            with document_path.open("rb") as handle:
                upload = client.post(
                    f"{base}/api/knowledge/documents",
                    files={"file": (document_path.name, handle)},
                )
            upload.raise_for_status()
            uploaded_doc = upload.json()
            print("PASS document indexed:", uploaded_doc["original_name"])

            catalog = client.post(
                f"{base}/api/chat",
                json={
                    "message": "What documents do you have loaded?",
                    "conversation_id": conversation_id,
                    "mode": "knowledge",
                },
            )
            catalog.raise_for_status()
            catalog_body = catalog.json()
            if (
                uploaded_doc["original_name"].lower()
                not in catalog_body["answer"].lower()
            ):
                return fail("catalog answer did not include uploaded document")
            if not catalog_body["evidence"]:
                return fail("catalog returned no evidence")
            print("PASS document catalog:", catalog_body["answer"])

        if uploaded_doc and args.question and args.expect:
            grounded = client.post(
                f"{base}/api/chat",
                json={
                    "message": args.question,
                    "conversation_id": conversation_id,
                    "mode": "knowledge",
                },
            )
            grounded.raise_for_status()
            grounded_body = grounded.json()

            if grounded_body["execution_class"] != "knowledge":
                return fail(
                    "document-grounded question did not route to KNOWLEDGE: "
                    f"{grounded_body['execution_class']}"
                )
            if args.expect.lower() not in grounded_body["answer"].lower():
                return fail(
                    f"Gate C answer did not contain expected value {args.expect!r}: "
                    f"{grounded_body['answer']}"
                )
            evidence = grounded_body["evidence"]
            if not evidence:
                return fail("Gate C returned no evidence")
            if not any(
                item.get("source_id") == uploaded_doc["id"] for item in evidence
            ):
                return fail(
                    "Gate C evidence did not reference the uploaded document"
                )

            print("PASS document-grounded answer:", grounded_body["answer"])
            print("PASS grounded evidence count:", len(evidence))

        if uploaded_doc and args.delete_after_test:
            document_id = uploaded_doc["id"]
            if not cleanup_owned_document(client, base, document_id, verify=True):
                return fail("acceptance document cleanup failed")
            print("PASS document deleted:", document_id)
            print("PASS deleted document absent from catalog")

            # The test-owned document is already gone. Clear the marker so the
            # finally block does not attempt a second deletion.
            uploaded_doc = None

            if args.question:
                after_delete = client.post(
                    f"{base}/api/chat",
                    json={
                        "message": args.question,
                        "conversation_id": conversation_id,
                    },
                )
                after_delete.raise_for_status()
                after_body = after_delete.json()
                if any(
                    item.get("source_id") == document_id
                    for item in after_body["evidence"]
                ):
                    return fail("deleted document was still returned as evidence")
                print(
                    "PASS deleted document no longer used as evidence:",
                    after_body["execution_class"],
                )

        print("ALL REQUESTED CHECKS PASSED")
        return 0
    finally:
        # If the formal run requested deletion, do not leave its test document
        # behind when Gate C, catalog, the model gateway, or another later
        # assertion fails. Only this run's own document id is touched.
        if uploaded_doc and args.delete_after_test:
            document_id = uploaded_doc.get("id")
            if document_id:
                if cleanup_owned_document(client, base, document_id, verify=True):
                    print("PASS acceptance document cleaned after failure")
                else:
                    print(
                        "WARN acceptance document could not be cleaned after failure"
                    )


if __name__ == "__main__":
    sys.exit(main())
