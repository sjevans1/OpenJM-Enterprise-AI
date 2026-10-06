"""VS4-C2: bounded CSV and self-contained HTML export rendering.

Pure rendering of ALREADY-PERSISTED bounded results. This module performs no
retrieval, SQL planning/execution, model call, external fetch or refresh. It
never reconstructs missing structure and never invents rows.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from html import escape

from app.schemas import Evidence

# Defensive ceilings mirrored from the persistence bounds; anything larger in
# storage is treated as unsupported rather than silently truncated.
MAX_EXPORT_BYTES = 262_144
MAX_COLUMNS = 128
MAX_ROWS = 5_000

# OWASP CSV-injection triggers. Neutralized only for untrusted *text* cells;
# genuinely typed numbers keep their value.
_FORMULA_PREFIXES = ("=", "+", "-", "@")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class ExportError(Exception):
    """Bounded export failure carrying the HTTP status the API must return."""

    status_code = 409


class ExportUnavailable(ExportError):
    """The persisted payload cannot support the requested export format."""

    status_code = 409


class ExportSelectionRequired(ExportError):
    """Several structured result sets exist; an explicit index is required."""

    status_code = 409


class ExportIndexInvalid(ExportError):
    """The supplied structured result index is not exportable."""

    status_code = 422


def safe_filename(title: str | None, suffix: str) -> str:
    """ASCII-only attachment filename; never reflects raw user text or headers."""
    slug = _SLUG_RE.sub("-", (title or "").strip().lower()).strip("-")[:60]
    if not slug:
        slug = "report"
    return f"{slug}-{suffix}"


def _neutralize_formula(text: str) -> str:
    """Prefix spreadsheet-formula text cells, including after leading controls."""
    for char in text:
        if char.isspace() or ord(char) < 0x20:
            continue
        return "'" + text if char in _FORMULA_PREFIXES else text
    return text


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        # Actual typed numeric values are retained; never neutralized.
        return str(value)
    if isinstance(value, str):
        return _neutralize_formula(value)
    # Non-scalar JSON inside a cell stays literal text, then neutralized.
    return _neutralize_formula(json.dumps(value, ensure_ascii=False))


def _structured_payload(item: Evidence) -> tuple[list[str], list[list[object]]]:
    """Parse a persisted structured passage; unsupported payloads fail closed."""
    raw = item.passage
    if not isinstance(raw, str) or not raw.strip():
        raise ExportUnavailable("Structured result payload is unavailable")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise ExportUnavailable("Structured result payload is unsupported") from None
    if not isinstance(payload, dict):
        raise ExportUnavailable("Structured result payload is unsupported")
    columns = payload.get("columns")
    rows = payload.get("rows")
    if (
        not isinstance(columns, list)
        or not columns
        or len(columns) > MAX_COLUMNS
        or any(not isinstance(column, str) or not column.strip() for column in columns)
    ):
        raise ExportUnavailable("Structured result columns are unsupported")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ExportUnavailable("Structured result rows are unsupported")
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise ExportUnavailable("Structured result rows are unsupported")
    return list(columns), list(rows)


def render_csv(evidence: list[Evidence], *, index: int | None = None) -> str:
    """Render one persisted structured result set as RFC-4180 CSV.

    ``index`` is the position of the structured result inside the evidence list.
    Multiple structured sets require an explicit, server-validated index; sets
    are never combined.
    """
    structured = [
        (position, item)
        for position, item in enumerate(evidence)
        if item.source_type == "structured_query"
    ]
    if not structured:
        raise ExportUnavailable("CSV export is unavailable for this report")

    if index is None:
        if len(structured) != 1:
            raise ExportSelectionRequired(
                "Select one structured result to export as CSV"
            )
        _position, item = structured[0]
    else:
        selected = [pair for pair in structured if pair[0] == index]
        if not selected:
            raise ExportIndexInvalid("Structured result index is not exportable")
        _position, item = selected[0]

    columns, rows = _structured_payload(item)

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([_csv_cell(column) for column in columns])
    for row in rows:
        writer.writerow([_csv_cell(value) for value in row])

    text = buffer.getvalue()
    if len(text.encode("utf-8")) > MAX_EXPORT_BYTES:
        raise ExportUnavailable("CSV export exceeds the bounded size")
    return text


def citation_labels(evidence: list[Evidence]) -> list[str]:
    """Match the UI's independent document/data citation numbering."""
    documents = 0
    data = 0
    labels: list[str] = []
    for item in evidence:
        if item.source_type == "document":
            documents += 1
            labels.append(f"[DOC {documents}]")
        elif item.source_type == "structured_query":
            data += 1
            labels.append(f"[DATA {data}]")
        else:
            labels.append("[EVIDENCE]")
    return labels


def _iso(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _evidence_block(evidence: list[Evidence]) -> str:
    if not evidence:
        return ""
    labels = citation_labels(evidence)
    parts = ['<section class="evidence"><h2>Evidence used</h2>']
    for label, item in zip(labels, evidence):
        parts.append('<article class="evidence-card">')
        parts.append(
            f'<h3><span class="citation">{escape(label)}</span> '
            f"{escape(item.title)}</h3>"
        )
        parts.append(
            f'<p class="source-ref">Source: {escape(item.source_id)}</p>'
        )
        if item.source_type == "structured_query":
            parts.append(_structured_block(item))
        else:
            parts.append(f'<pre class="passage">{escape(item.passage)}</pre>')
        parts.append("</article>")
    parts.append("</section>")
    return "\n".join(parts)


def _structured_block(item: Evidence) -> str:
    """Render a structured passage as a table when it is well formed."""
    try:
        payload = json.loads(item.passage)
        columns = payload["columns"]
        rows = payload["rows"]
        if not isinstance(columns, list) or not isinstance(rows, list):
            raise ValueError
    except (TypeError, ValueError, KeyError):
        return f'<pre class="passage">{escape(item.passage)}</pre>'

    head = "".join(f"<th>{escape(str(column))}</th>" for column in columns)
    body = []
    for row in rows[:MAX_ROWS]:
        cells = "".join(f"<td>{escape(str(value))}</td>" for value in row)
        body.append(f"<tr>{cells}</tr>")
    return (
        '<table class="structured-result"><thead><tr>'
        f"{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


_HTML_STYLE = """
:root { color-scheme: light; }
body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
       margin: 2rem auto; max-width: 52rem; line-height: 1.5; color: #1a1a1a; }
h1 { font-size: 1.4rem; margin-bottom: .25rem; }
h2 { font-size: 1.05rem; margin-top: 1.75rem; border-bottom: 1px solid #d0d0d0;
     padding-bottom: .25rem; }
h3 { font-size: .95rem; margin: .75rem 0 .25rem; }
.meta { color: #444; font-size: .85rem; }
.meta dt { font-weight: 600; }
.meta div { margin: .1rem 0; }
.answer { white-space: pre-wrap; border-left: 3px solid #b89b3a; padding-left: .75rem; }
.citation { font-weight: 700; }
.source-ref { color: #555; font-size: .8rem; margin: .1rem 0 .35rem; }
pre.passage, .source-ref { overflow-wrap: anywhere; white-space: pre-wrap; }
.evidence-card { border: 1px solid #e0e0e0; border-radius: 6px;
                 padding: .6rem .8rem; margin: .6rem 0; }
table.structured-result { border-collapse: collapse; font-size: .85rem; }
table.structured-result th, table.structured-result td {
  border: 1px solid #d0d0d0; padding: .25rem .5rem; text-align: left; }
footer { margin-top: 2rem; color: #555; font-size: .8rem;
         border-top: 1px solid #e0e0e0; padding-top: .75rem; }
@media print {
  body { margin: 0; max-width: none; }
  .evidence-card, table.structured-result tr { break-inside: avoid; }
  h2 { break-after: avoid; }
}
"""


def render_html(
    *,
    title: str,
    identity: list[tuple[str, str]],
    as_of: datetime | None,
    answer: str,
    evidence: list[Evidence],
    provenance_note: str,
) -> str:
    """Self-contained, fully escaped HTML. No scripts, no remote resources."""
    rows = "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>"
        for label, value in identity
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{escape(title)}</title>
<style>{_HTML_STYLE}</style>
</head>
<body>
<header>
<h1>{escape(title)}</h1>
<div class="meta">
<div><dt>Historical as-of</dt><dd>{escape(_iso(as_of))}</dd></div>
{rows}
</div>
</header>
<main>
<section class="answer-section">
<h2>Answer</h2>
<div class="answer">{escape(answer)}</div>
</section>
{_evidence_block(evidence)}
</main>
<footer>{escape(provenance_note)}</footer>
</body>
</html>
"""
