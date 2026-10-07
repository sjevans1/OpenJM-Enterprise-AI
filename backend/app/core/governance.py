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
