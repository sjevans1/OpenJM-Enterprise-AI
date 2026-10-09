"""BV5-A chat artifact storage and metadata.

A controlled storage abstraction for downloadable Chat work products. Two
invariants hold throughout:

* **Storage keys are opaque and server-generated.** A caller can never supply a
  filesystem path, so no ``..``/absolute/backslash name can escape the store,
  and the resolved path is re-checked to stay directly under the storage root.
* **Content is inert data.** It is written verbatim and served only as an
  attachment by the API layer. Nothing here executes, templates, shells out to,
  or fetches anything.

No arbitrary shell or network execution exists on this path.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings

# The supported formats and the single MIME type each maps to. This mapping is
# the allow-list vocabulary; ``ensure_allowed`` additionally intersects it with
# the operator-configured ``artifact_allowed_mime_types``.
FORMAT_MIME: dict[str, str] = {
    "html": "text/html",
    "markdown": "text/markdown",
    "text": "text/plain",
    "csv": "text/csv",
}
FORMAT_EXTENSION: dict[str, str] = {
    "html": "html",
    "markdown": "md",
    "text": "txt",
    "csv": "csv",
}

_KEY_RE = re.compile(r"^[0-9a-f]{32}$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:")

MAX_COLUMNS = 128
MAX_ROWS = 5_000


class ArtifactError(Exception):
    """Bounded artifact failure carrying the HTTP status the API must return."""

    status_code = 400


class ArtifactUnsupported(ArtifactError):
    status_code = 422


class ArtifactTooLarge(ArtifactError):
    status_code = 413


class ArtifactNameError(ArtifactError):
    """A filename or storage key that could address outside one directory."""

    status_code = 422


def resolve_mime(fmt: str) -> str:
    """The MIME type for a supported artifact format, or fail closed."""
    mime = FORMAT_MIME.get(fmt)
    if mime is None:
        raise ArtifactUnsupported("Unsupported artifact format")
    return mime


def allowed_mimes() -> set[str]:
    return set(get_settings().artifact_allowed_mime_list)


def ensure_allowed(fmt: str) -> str:
    """Resolve and authorize a format's MIME against the configured allow-list."""
    mime = resolve_mime(fmt)
    if mime not in allowed_mimes():
        raise ArtifactUnsupported("Artifact MIME type is not permitted")
    return mime


def validate_stored_filename(name: str) -> str:
    """Reject any stored filename that could address outside one directory.

    Rejects separators (``/`` and ``\\``), NUL, Windows drive prefixes, absolute
    paths, ``.``/``..``/``..``-prefixed names and any name that is not already a
    single path component.
    """
    if not isinstance(name, str) or not name or len(name) > 240:
        raise ArtifactNameError("Unsafe artifact filename")
    if "\x00" in name or "/" in name or "\\" in name:
        raise ArtifactNameError("Unsafe artifact filename")
    if _WINDOWS_DRIVE_RE.match(name):
        raise ArtifactNameError("Unsafe artifact filename")
    if os.path.isabs(name) or name in (".", "..") or name.startswith(".."):
        raise ArtifactNameError("Unsafe artifact filename")
    if Path(name).name != name:
        raise ArtifactNameError("Unsafe artifact filename")
    return name


def safe_filename(title: str, fmt: str) -> str:
    """Derive a single-segment attachment filename from a title.

    The title is slugged (so the result cannot contain a separator) and then
    validated, giving defense in depth against a hostile title.
    """
    extension = FORMAT_EXTENSION.get(fmt)
    if extension is None:
        raise ArtifactUnsupported("Unsupported artifact format")
    slug = _SLUG_RE.sub("-", (title or "").strip().lower()).strip("-")[:60]
    if not slug:
        slug = "artifact"
    return validate_stored_filename(f"{slug}.{extension}")


def new_storage_key() -> str:
    """A fresh opaque key. It is never derived from user input."""
    return uuid.uuid4().hex


def validate_storage_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise ArtifactNameError("Invalid artifact storage key")
    return key


class ArtifactStorage:
    """Opaque-key blob store rooted at exactly one configured directory."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        validate_storage_key(key)
        root = self._root.resolve()
        path = (root / key).resolve()
        # Defense in depth: the resolved target must be a direct child of root.
        if path.parent != root:
            raise ArtifactNameError("Artifact key escapes the storage root")
        return path

    def write(self, key: str, data: bytes) -> int:
        path = self._path(key)
        self._root.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return len(data)

    def read(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise ArtifactError("Artifact content is unavailable") from None

    def delete(self, key: str) -> bool:
        path = self._path(key)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False


def storage() -> ArtifactStorage:
    return ArtifactStorage(get_settings().artifacts_dir)


def _csv_is_tabular(content: str) -> bool:
    """A CSV artifact must be a genuine, bounded, rectangular table."""
    rows = [row for row in csv.reader(io.StringIO(content)) if row]
    if not rows or len(rows) > MAX_ROWS:
        return False
    width = len(rows[0])
    if width == 0 or width > MAX_COLUMNS:
        return False
    return all(len(row) == width for row in rows)


def validate_content(fmt: str, content: str) -> bytes:
    """Validate inert content and return its UTF-8 bytes.

    CSV artifacts must be tabular. HTML/Markdown/TXT are stored as data and are
    never parsed, executed or templated here.
    """
    if not isinstance(content, str) or not content:
        raise ArtifactUnsupported("Artifact content is empty")
    if fmt == "csv" and not _csv_is_tabular(content):
        raise ArtifactUnsupported("CSV artifact must be a non-empty table")
    data = content.encode("utf-8")
    if len(data) > get_settings().max_artifact_bytes:
        raise ArtifactTooLarge("Artifact exceeds the bounded size")
    return data


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def provenance_payload(evidence, *, now: datetime | None = None) -> tuple[bool, str]:
    """Build the metadata-only provenance record for an artifact.

    Evidence-backed artifacts preserve their citations and an as-of timestamp.
    The record ALWAYS states that the artifact is not approved and not
    authoritative: it is a Chat work product, not a Governed Saved Report.
    """
    created = now or datetime.now(timezone.utc)
    citations = [
        {
            "source_type": item.source_type,
            "source_id": item.source_id,
            "title": item.title,
            "evidence_id": item.evidence_id,
        }
        for item in evidence
    ]
    payload = {
        "kind": "chat_artifact",
        "label": "Chat artifact (not a governed report)",
        "approved": False,
        "authoritative": False,
        "is_evidence_backed": bool(citations),
        "citations": citations,
        "as_of": created.isoformat(),
        "note": (
            "Metadata only. Not approved and not authoritative; this is not a "
            "Governed Saved Report."
        ),
    }
    return bool(citations), json.dumps(payload)
