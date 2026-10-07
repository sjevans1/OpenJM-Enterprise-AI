"""Explicit platform-operator authorization (BV1-A).

OpenJM platform operators are a small, explicitly granted trusted set. Platform
authority is a separate axis from the tenant roles (``viewer``/``editor``/
``admin``/``owner``): a tenant role never grants a platform capability, and a
platform capability is never satisfied by a tenant role.

The capabilities are split so that controlling platform machinery is
independent of reaching into a customer's governed content:

* ``METADATA_READ`` reads control-plane metadata and status, never content.
* ``TENANTS_ADMIN`` creates or suspends tenants.
* ``OPERATORS_ADMIN`` grants or revokes platform capabilities.
* ``CONTENT_SUPPORT`` is the only capability that can reach customer content and
  is always granted explicitly. No metadata capability implies it.

This module is deliberately dependency free (no FastAPI, no SQLAlchemy) so it can
be reasoned about, unit tested and reused by the API layer, the identity
resolution path and operator tooling alike.
"""

from __future__ import annotations

from enum import Enum


class PlatformCapability(str, Enum):
    """A single platform (control-plane) capability, evaluated per request."""

    METADATA_READ = "platform:metadata:read"
    TENANTS_ADMIN = "platform:tenants:admin"
    OPERATORS_ADMIN = "platform:operators:admin"
    # The only capability that reaches customer content. Always explicit.
    CONTENT_SUPPORT = "platform:content:support"


PLATFORM_CAPABILITIES: tuple[str, ...] = tuple(c.value for c in PlatformCapability)

# Capabilities that can reach a customer's governed content. Kept as an explicit
# set so the separation invariant is testable rather than implied.
CONTENT_CAPABILITIES: frozenset[PlatformCapability] = frozenset(
    {PlatformCapability.CONTENT_SUPPORT}
)

# Convenience tiers an operator-provisioning flow may use. ``CONTENT_SUPPORT`` is
# deliberately absent from every tier: metadata administration never implies
# customer-content access, so content support is granted, and audited, on its own.
PLATFORM_TIER_OBSERVER = "platform-observer"
PLATFORM_TIER_ADMIN = "platform-admin"

PLATFORM_TIERS: tuple[str, ...] = (PLATFORM_TIER_OBSERVER, PLATFORM_TIER_ADMIN)

PLATFORM_TIER_CAPABILITIES: dict[str, frozenset[PlatformCapability]] = {
    PLATFORM_TIER_OBSERVER: frozenset({PlatformCapability.METADATA_READ}),
    PLATFORM_TIER_ADMIN: frozenset(
        {
            PlatformCapability.METADATA_READ,
            PlatformCapability.TENANTS_ADMIN,
            PlatformCapability.OPERATORS_ADMIN,
        }
    ),
}


def is_known_platform_capability(value: object) -> bool:
    return isinstance(value, str) and value in PLATFORM_CAPABILITIES


def platform_capabilities_for_tier(tier: str | None) -> frozenset[PlatformCapability]:
    """Resolve a platform tier to its capability set.

    An unknown or missing tier yields the empty set. Callers must treat "no
    capabilities" as deny, never as "no restrictions".
    """
    if not tier:
        return frozenset()
    return PLATFORM_TIER_CAPABILITIES.get(tier, frozenset())


def normalize_platform_capabilities(values) -> frozenset[PlatformCapability]:
    """Validate a grant request into a capability set.

    Unknown capability strings are a hard error at grant time rather than being
    silently dropped: a typo must not quietly downgrade a grant, and a caller
    must never be able to grant an undefined capability.
    """
    normalized: set[PlatformCapability] = set()
    for value in values or ():
        raw = value.value if isinstance(value, PlatformCapability) else value
        if not is_known_platform_capability(raw):
            raise ValueError(f"Unknown platform capability: {raw!r}")
        normalized.add(PlatformCapability(raw))
    return frozenset(normalized)


def capabilities_grant_content_access(capabilities) -> bool:
    """Does this capability set permit reaching customer content?"""
    normalized = {
        c.value if isinstance(c, PlatformCapability) else c for c in (capabilities or ())
    }
    return bool(normalized & CONTENT_CAPABILITIES)
