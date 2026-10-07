"""Information-governance vocabulary shared by BV1 increments.

BV1 separates two questions:

* RBAC / capabilities decide *what a principal may do* (this is
  ``app/core/permissions.py`` plus ``app/core/platform.py``).
* Source-level classification and group policy decide *which governed
  information a principal may access*.

BV1-A delivers the group/department/steward model and the resolution of a
principal's effective scopes. The source classification vocabulary itself and
the pre-retrieval enforcement that consumes it land in BV1-B (Knowledge) and
BV1-C (Data/Hybrid/Reports), which reuse the scope identifiers defined here.

This module is deliberately dependency free so it can be reasoned about, unit
tested and reused by services, migrations and the API layer alike.
"""

from __future__ import annotations

from enum import Enum


class StewardScopeType(str, Enum):
    """The governed scope a delegated data steward is authorized over."""

    TENANT = "tenant"
    DEPARTMENT = "department"
    GROUP = "group"


STEWARD_SCOPE_TYPES: tuple[str, ...] = tuple(s.value for s in StewardScopeType)


def is_known_steward_scope_type(value: object) -> bool:
    return isinstance(value, str) and value in STEWARD_SCOPE_TYPES


class SourceClassification(str, Enum):
    """Controlled initial classification vocabulary for governed sources.

    Business-friendly and deliberately small for this increment. The values are
    persisted in audit records and on source rows, so they must not be renamed
    casually. A tenant may later add custom labels; this increment does not.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    HIGHLY_RESTRICTED = "highly_restricted"


SOURCE_CLASSIFICATIONS: tuple[str, ...] = tuple(c.value for c in SourceClassification)

# The safe default for a source with no explicit policy. ``internal`` is chosen
# because it matches the accepted pre-BV1 behavior (an authenticated tenant
# member could rely on the source), so existing rows do not change meaning.
DEFAULT_SOURCE_CLASSIFICATION = SourceClassification.INTERNAL.value

# Classifications that may rely on the tenant-wide fallback. The stricter two
# never use it: they are explicit grant only.
TENANT_WIDE_CLASSIFICATIONS: frozenset[str] = frozenset(
    {SourceClassification.PUBLIC.value, SourceClassification.INTERNAL.value}
)


def is_known_classification(value: object) -> bool:
    return isinstance(value, str) and value in SOURCE_CLASSIFICATIONS


def normalize_classification(value: object) -> str:
    """Coerce a stored classification to a known value, failing closed.

    An unknown, missing or malformed value resolves to ``highly_restricted``,
    the most restrictive class, so a corrupt or future label never widens access.
    """
    if is_known_classification(value):
        return str(value)
    return SourceClassification.HIGHLY_RESTRICTED.value
