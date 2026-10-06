"""Tenant constants shared by the ORM, migrations and the dev bootstrap.

``LEGACY_TENANT_ID`` is the tenant every pre-VS5 row is adopted into. It is a
pure migration/back-compat identifier; production deployments create their own
tenants through the identity layer and never rely on it.
"""

from __future__ import annotations

LEGACY_TENANT_ID = "tnt-local"
LEGACY_TENANT_SLUG = "local"
LEGACY_TENANT_NAME = "Local workspace"

# The single local development principal. VS5 keeps it working for local
# development and the existing test suite, but it is now provisioned as a real
# tenant membership resolved server-side, not as a bare settings string.
LEGACY_PRINCIPAL_ID = "local-admin"
LEGACY_PRINCIPAL_SUBJECT = "local:local-admin"

# Lifecycle states for the document lifecycle (#6).
DOC_STATE_PENDING = "pending"
DOC_STATE_INDEXING = "indexing"
DOC_STATE_READY = "ready"
DOC_STATE_FAILED = "failed"
DOC_STATE_DELETING = "deleting"
DOC_STATE_DELETED = "deleted"

DOC_STATES_TERMINAL = frozenset({DOC_STATE_READY, DOC_STATE_FAILED, DOC_STATE_DELETED})
DOC_STATES_RETRIEVABLE = frozenset({DOC_STATE_READY})
