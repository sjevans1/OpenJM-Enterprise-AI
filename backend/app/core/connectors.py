"""OpenJM-owned connector registry.

This module is deliberately dependency-free (no FastAPI, no SQLAlchemy, no
network client). It describes *what a connector type is allowed to do* so that
every other layer, the API, the scheduler, the action runtime and the tests, can
reason about connectors without importing a provider implementation.

Design rules, mirroring the VS6 tool registry:

* A connector type declares its capabilities explicitly. Nothing is implied.
* Network access exists only inside a registered connector implementation.
  There is no generic URL fetch, no generic REST call and no generic GraphQL
  call anywhere in the platform, and this registry is what makes that checkable.
* Every externally callable operation must be declared here and mediated by
  OpenJM policy. A connector must never be an escape hatch around the VS6
  action runtime.
* Read and write are separated at declaration time. A write capability is
  approval-gated downstream; declaring one here does not authorize it.
* Version identifiers are stable and are persisted in audit records, so they
  must not be renamed casually.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ConnectorCapability(str, Enum):
    """A coarse class of external resource a connector can work with."""

    DOCUMENTS = "documents"
    RECORDS = "records"
    EVENTS = "events"
    ACTIONS = "actions"
    NOTIFICATIONS = "notifications"
    # The connector can answer "does this mapped user currently have access to
    # this resource" against the provider. This is what enables current-user
    # authorization rather than cached authorization.
    PERMISSIONS = "permissions"


class OperationClass(str, Enum):
    """Read/write classification, aligned with the VS6 operation classes."""

    READ = "read"
    WRITE = "write"


class AuthorizationBehavior(str, Enum):
    """How a connector can prove a *current end user* may see a resource.

    ``PROVIDER_CURRENT_STATE``
        The provider exposes a permission/current-state API and the connector
        revalidates per end user at retrieval time. This is the strongest mode
        and the only one that can serve user-specific evidence.
    ``CONNECTOR_SCOPED``
        The service credential's own scope is the whole boundary; the provider
        has no per-user notion. Everything the credential can read is treated as
        tenant-wide evidence, never user-specific.
    ``NONE``
        No authorization model is available. User-specific evidence is refused.
    """

    PROVIDER_CURRENT_STATE = "provider_current_state"
    CONNECTOR_SCOPED = "connector_scoped"
    NONE = "none"


class IdempotencyMode(str, Enum):
    """How repeat processing of the same external change is made safe."""

    # Provider supplies a revision/version per resource: compare and skip.
    PROVIDER_REVISION = "provider_revision"
    # No revision: fall back to a content hash of the fetched payload.
    CONTENT_HASH = "content_hash"
    # The provider supports caller-supplied idempotency keys on mutations.
    PROVIDER_KEY = "provider_key"


@dataclass(frozen=True)
class DeclaredOperation:
    """One externally callable operation a connector type exposes.

    An operation that is not declared here cannot be invoked, regardless of what
    the model proposes.
    """

    name: str
    capability: ConnectorCapability
    operation_class: OperationClass
    description: str
    # Permission a caller must hold in OpenJM before this is even considered.
    required_permission: str
    # True when the provider itself must confirm the *end user* (not just the
    # service credential) may perform this operation.
    requires_user_authorization: bool = True
    # Writes are approval-gated through the VS6 runtime. Reads are not.
    # This defaults to the operation class and should not be weakened.
    requires_approval: bool | None = None
    timeout_seconds: int = 30
    idempotency: IdempotencyMode = IdempotencyMode.PROVIDER_REVISION

    @property
    def is_write(self) -> bool:
        return self.operation_class is OperationClass.WRITE

    @property
    def approval_required(self) -> bool:
        """Writes require approval unless a declaration explicitly says not to.

        A declaration may only opt out of approval for a write when the write is
        inherently idempotent and non-mutating in the provider's terms. That is
        rare and must be justified at the declaration site.
        """
        if self.requires_approval is None:
            return self.is_write
        return bool(self.requires_approval)


@dataclass(frozen=True)
class ConnectorTypeSpec:
    """The declared contract of one connector type at one version."""

    type_id: str
    version: str
    display_name: str
    description: str

    capabilities: frozenset[ConnectorCapability]
    operations: tuple[DeclaredOperation, ...]

    authorization_behavior: AuthorizationBehavior
    requires_user_mapping: bool

    # Credential shape. Only the *names* of the fields are declared; values live
    # encrypted in the OpenJM credential boundary and are never returned.
    credential_kind: str
    credential_fields: tuple[str, ...]
    credential_rotation: str

    event_support: bool
    reconciliation_support: bool
    incremental_support: bool

    supports_test_connection: bool
    timeout_seconds: int
    max_retries: int
    # Bound on how many resources one initial sync may enumerate. Prevents an
    # accidental unbounded crawl of an external system.
    initial_sync_limit: int = 500

    def __post_init__(self) -> None:
        if not self.type_id or not self.version:
            raise ValueError("a connector type requires a type_id and a version")
        if not self.credential_fields:
            raise ValueError(f"connector {self.type_id} declares no credential fields")
        names = [op.name for op in self.operations]
        if len(names) != len(set(names)):
            raise ValueError(f"connector {self.type_id} declares a duplicate operation name")
        for op in self.operations:
            if op.capability not in self.capabilities:
                raise ValueError(
                    f"operation '{op.name}' uses capability '{op.capability.value}' "
                    f"which {self.type_id} does not declare"
                )
            if not op.required_permission:
                raise ValueError(f"operation '{op.name}' declares no required permission")

    @property
    def key(self) -> str:
        return f"{self.type_id}@{self.version}"

    def read_operations(self) -> tuple[DeclaredOperation, ...]:
        return tuple(op for op in self.operations if not op.is_write)

    def write_operations(self) -> tuple[DeclaredOperation, ...]:
        return tuple(op for op in self.operations if op.is_write)

    def operation(self, name: str) -> DeclaredOperation:
        for op in self.operations:
            if op.name == name:
                return op
        raise ConnectorRegistryError(
            f"connector {self.key} has no operation '{name}'", code="unregistered_operation"
        )

    def describe(self) -> dict:
        """Public metadata. Contains no secrets by construction."""
        return {
            "type_id": self.type_id,
            "version": self.version,
            "key": self.key,
            "display_name": self.display_name,
            "description": self.description,
            "capabilities": sorted(c.value for c in self.capabilities),
            "authorization_behavior": self.authorization_behavior.value,
            "requires_user_mapping": self.requires_user_mapping,
            "credential_kind": self.credential_kind,
            "credential_fields": list(self.credential_fields),
            "credential_rotation": self.credential_rotation,
            "event_support": self.event_support,
            "reconciliation_support": self.reconciliation_support,
            "incremental_support": self.incremental_support,
            "supports_test_connection": self.supports_test_connection,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "initial_sync_limit": self.initial_sync_limit,
            "operations": [
                {
                    "name": op.name,
                    "capability": op.capability.value,
                    "operation_class": op.operation_class.value,
                    "description": op.description,
                    "required_permission": op.required_permission,
                    "requires_user_authorization": op.requires_user_authorization,
                    "requires_approval": op.approval_required,
                    "timeout_seconds": op.timeout_seconds,
                    "idempotency": op.idempotency.value,
                }
                for op in self.operations
            ],
        }


class ConnectorRegistryError(Exception):
    """A connector type or operation could not be resolved."""

    def __init__(self, message: str, *, code: str = "connector_registry_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ConnectorRegistry:
    """The set of connector types this deployment knows about.

    Registration is explicit. There is no plugin discovery and no directory
    scan, so an unknown package cannot introduce network access merely by being
    importable.
    """

    _types: dict[str, ConnectorTypeSpec] = field(default_factory=dict)

    def register(self, spec: ConnectorTypeSpec) -> None:
        if spec.key in self._types:
            raise ConnectorRegistryError(
                f"connector type {spec.key} is already registered", code="duplicate_connector_type"
            )
        self._types[spec.key] = spec

    def unregister(self, key: str) -> None:
        """Remove a registration. Used only by tests to keep registries isolated."""
        self._types.pop(key, None)

    def get(self, type_id: str, version: str | None = None) -> ConnectorTypeSpec:
        if version:
            key = f"{type_id}@{version}"
            spec = self._types.get(key)
            if spec is None:
                raise ConnectorRegistryError(
                    f"unknown connector type {key}", code="unknown_connector_type"
                )
            return spec
        matches = [spec for spec in self._types.values() if spec.type_id == type_id]
        if not matches:
            raise ConnectorRegistryError(
                f"unknown connector type {type_id}", code="unknown_connector_type"
            )
        # Deterministic: highest declared version string wins.
        return sorted(matches, key=lambda item: item.version)[-1]

    def has(self, type_id: str, version: str | None = None) -> bool:
        try:
            self.get(type_id, version)
        except ConnectorRegistryError:
            return False
        return True

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._types))

    def specs(self) -> tuple[ConnectorTypeSpec, ...]:
        return tuple(self._types[key] for key in self.keys())

    def describe(self) -> list[dict]:
        return [spec.describe() for spec in self.specs()]


# The process-wide registry. Provider modules register themselves into this and
# the application imports them at start-up; nothing else may add a connector.
connector_registry = ConnectorRegistry()
