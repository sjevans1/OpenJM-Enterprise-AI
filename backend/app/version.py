"""Single source of product/release identity (VS8 Workstream H).

The version is surfaced in the API (``/api/version``, ``/api/health``), the
public white-label config and logs, so it is defined in exactly one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings

# Canonical product version. Package/build manifests are machine-checked against
# this value by the REL1-A acceptance gate.
PRODUCT_VERSION = "0.2.0"
# The release programme that produced this build.
RELEASE_TRAIN = "REL1"


def build_info(settings: "Settings") -> dict:
    """Release identity, safe for unauthenticated exposure (no secrets)."""
    return {
        "product": settings.product_name,
        "version": PRODUCT_VERSION,
        "release_train": RELEASE_TRAIN,
        "release_id": settings.release_id or None,
        "profile": settings.deployment_profile,
    }
