"""Single source of product/release identity (VS8 Workstream H).

The version is surfaced in the API (``/api/version``, ``/api/health``), the
public white-label config and logs, so it is defined in exactly one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings

# Keep in step with backend/pyproject.toml and frontend/package.json.
PRODUCT_VERSION = "0.2.0"
# The vertical-slice / programme that produced this build.
RELEASE_TRAIN = "VS8"


def build_info(settings: "Settings") -> dict:
    """Release identity, safe for unauthenticated exposure (no secrets)."""
    return {
        "product": settings.product_name,
        "version": PRODUCT_VERSION,
        "release_train": RELEASE_TRAIN,
        "release_id": settings.release_id or None,
        "profile": settings.deployment_profile,
    }
