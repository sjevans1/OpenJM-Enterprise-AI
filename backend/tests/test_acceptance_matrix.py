"""Machine-check that the Issue #45 acceptance matrix keeps its required shape.

The product-completion acceptance matrix (docs/ACCEPTANCE_MATRIX.md) is a living
document. Its value depends on the four persona sections and the named-area
close-out surviving edits. This test does not judge the status of any row; it
only fails if a section is missing, so a future edit cannot silently drop a
persona or an acceptance area.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX = REPO_ROOT / "docs" / "ACCEPTANCE_MATRIX.md"

PERSONA_HEADINGS = (
    "## Persona: End user",
    "## Persona: Data steward",
    "## Persona: Client admin",
    "## Persona: OpenJM operator",
)

# Each named area from the lane brief must be represented, using a stable token
# rather than exact prose so wording can be refined without weakening the check.
AREA_TOKENS = (
    "Chat-first UX",
    "Source authorization",
    "Chat artifact end-to-end flow",
    "Governed reports",
    "Client-admin management",
    "Platform-admin boundaries",
    "Delegated support",
    "Metering / entitlements",
    "Admission / capacity",
    "Backup / recovery",
    "Installer / productization",
    "Rahkia qualification",
)


@pytest.fixture(scope="module")
def matrix_text() -> str:
    return MATRIX.read_text(encoding="utf-8")


def test_acceptance_matrix_exists() -> None:
    assert MATRIX.is_file(), f"missing acceptance matrix at {MATRIX}"


def test_acceptance_matrix_is_stamped(matrix_text: str) -> None:
    # The matrix must carry a base SHA and a date so a stale row cannot be
    # mistaken for a live one.
    assert "Observed at `" in matrix_text
    assert "2026-" in matrix_text


@pytest.mark.parametrize("heading", PERSONA_HEADINGS)
def test_acceptance_matrix_has_persona_section(matrix_text: str, heading: str) -> None:
    assert heading in matrix_text, f"missing persona section: {heading}"


@pytest.mark.parametrize("token", AREA_TOKENS)
def test_acceptance_matrix_covers_named_area(matrix_text: str, token: str) -> None:
    assert token in matrix_text, f"missing named area: {token}"
