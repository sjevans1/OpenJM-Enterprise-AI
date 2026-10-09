"""BV5-B: controlled PDF and DOCX rendering for Chat artifacts.

Turn an existing Chat artifact's already-authorized, inert content plus its
provenance metadata into a real PDF or DOCX file. Three invariants hold,
mirroring ``app.services.artifacts``:

* **Content is data.** It is laid out as literal text; nothing here executes,
  parses it as a template, or honours markup. A hostile ``<script>``/HTML
  fragment or a ``{{...}}``/``${...}`` token is rendered as inert characters,
  never evaluated.
* **No remote asset is ever fetched.** An external URL or ``<img src=...>``
  reference inside content is rendered as text. There is no socket, no HTTP
  client, and no asset resolver on this path.
* **The output is bounded.** The rendered bytes are size-checked against the
  configured ``max_artifact_bytes`` before they are returned (and before any
  future persisted flow would write anything).

These renderers are a pure transform. They never read or write the artifact
store themselves and never mutate the database, so a render failure cannot
leave a phantom artifact behind: the caller simply gets an error.

Why render-on-request rather than persisting a new ``pdf``/``docx`` row: the
``chat_artifacts`` table's ``ck_chat_artifact_format`` CHECK constraint admits
exactly the four storable formats (``html``/``markdown``/``text``/``csv``).
``pdf`` and ``docx`` are RENDER-ONLY targets; adding them as stored formats
would require a schema change outside this package's allowed surface.
"""

from __future__ import annotations

import io
import re

from xml.sax.saxutils import escape

from app.services import artifacts as artifacts_service
from app.services.artifacts import (
    ArtifactError,
    ArtifactTooLarge,
    ArtifactUnsupported,
)

# The only targets this renderer produces. ``pdf`` and ``docx`` are also in the
# shared ``artifacts`` format/MIME vocabulary (``FORMAT_MIME``/``FORMAT_EXTENSION``).
RENDER_TARGETS: tuple[str, ...] = ("pdf", "docx")
RENDER_MIME: dict[str, str] = {
    target: artifacts_service.FORMAT_MIME[target] for target in RENDER_TARGETS
}

# Defense in depth against a pathological body: bound how many lines are laid
# out regardless of the (already size-bounded) source content.
MAX_RENDER_LINES = 20_000

# C0/C1 control characters other than TAB/LF/CR are illegal in XML and neither
# printable nor safe in a document. They are stripped, not escaped.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

DISCLAIMER = (
    "Chat artifact (not a governed report). Not approved and not authoritative."
)
PROVENANCE_HEADING = "Evidence & provenance"


class ArtifactRenderError(ArtifactError):
    """A bounded render failure the API turns into an error response."""

    status_code = 422


def _sanitize(text: object) -> str:
    """Coerce arbitrary content to inert document text.

    Control characters are removed and newlines normalised. No markup,
    template syntax or code is interpreted: the result is literal text.
    """
    if not isinstance(text, str):
        return ""
    cleaned = _CONTROL_RE.sub("", text)
    return cleaned.replace("\r\n", "\n").replace("\r", "\n")


def _citation_rows(provenance: object) -> list[dict]:
    if not isinstance(provenance, dict):
        return []
    rows = provenance.get("citations")
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "title": _sanitize(str(row.get("title") or "")),
                "source_type": _sanitize(str(row.get("source_type") or "")),
                "source_id": _sanitize(str(row.get("source_id") or "")),
                "evidence_id": row.get("evidence_id"),
            }
        )
    return out


def _as_of(provenance: object) -> str | None:
    if not isinstance(provenance, dict):
        return None
    value = provenance.get("as_of")
    if not value:
        return None
    return _sanitize(str(value))


def _citation_label(citation: dict) -> str:
    parts = " ".join(
        part for part in (citation.get("source_type"), citation.get("source_id")) if part
    )
    title = citation.get("title") or ""
    label = f"{title} ({parts})".strip() if parts else title
    if citation.get("evidence_id"):
        label = f"{label} [{citation['evidence_id']}]"
    return label or "(citation)"


def _body_lines(content: str) -> list[str]:
    lines = content.split("\n")
    if len(lines) > MAX_RENDER_LINES:
        lines = lines[:MAX_RENDER_LINES]
        lines.append("[content truncated]")
    return lines


def _render_pdf(
    title: str, body_lines: list[str], citations: list[dict], as_of: str | None
) -> bytes:
    # Imported lazily so importing this module never requires reportlab.
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, title=title)
    story = [Paragraph(escape(title) or "Artifact", styles["Title"]), Spacer(1, 12)]
    for line in body_lines:
        # escape() makes the line inert XML text: '<script>' stays literal.
        story.append(Paragraph(escape(line), body) if line else Spacer(1, 6))
    if citations or as_of:
        story.append(Spacer(1, 12))
        story.append(Paragraph(PROVENANCE_HEADING, styles["Heading2"]))
        for citation in citations:
            story.append(Paragraph("- " + escape(_citation_label(citation)), body))
        if as_of:
            story.append(Paragraph("As of: " + escape(as_of), body))
        story.append(Paragraph(escape(DISCLAIMER), body))
    doc.build(story)
    return buffer.getvalue()


def _render_docx(
    title: str, body_lines: list[str], citations: list[dict], as_of: str | None
) -> bytes:
    # Imported lazily so importing this module never requires python-docx.
    from docx import Document

    doc = Document()
    doc.add_heading(title or "Artifact", level=0)
    for line in body_lines:
        # python-docx writes text as inert runs; no markup is interpreted.
        doc.add_paragraph(line)
    if citations or as_of:
        doc.add_heading(PROVENANCE_HEADING, level=1)
        for citation in citations:
            doc.add_paragraph(_citation_label(citation))
        if as_of:
            doc.add_paragraph(f"As of: {as_of}")
        doc.add_paragraph(DISCLAIMER)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def render_artifact(
    *,
    content: str,
    provenance: object,
    target: str,
    title: str,
) -> bytes:
    """Render one artifact's content + provenance to ``target`` bytes.

    Raises ``ArtifactUnsupported`` for an unknown target and ``ArtifactTooLarge``
    when the rendered bytes exceed the configured ``max_artifact_bytes`` bound.
    """
    if target not in RENDER_TARGETS:
        raise ArtifactUnsupported("Unsupported render target")

    safe_title = _sanitize(title)[:240] or "Artifact"
    body_lines = _body_lines(_sanitize(content))
    citations = _citation_rows(provenance)
    as_of = _as_of(provenance)

    if target == "pdf":
        data = _render_pdf(safe_title, body_lines, citations, as_of)
    else:  # "docx" — the only other member of RENDER_TARGETS.
        data = _render_docx(safe_title, body_lines, citations, as_of)

    # Bounded resource usage: fail before the bytes are returned anywhere.
    if len(data) > artifacts_service.get_settings().max_artifact_bytes:
        raise ArtifactTooLarge("Rendered artifact exceeds the bounded size")
    return data
