"""Deterministic tool registry for the bounded action runtime (VS6).

The registry is the *only* place a capability can be declared. A model can name
a tool; it can never introduce one, widen one, or reach outside the declared
metadata. Every field the runtime needs in order to decide safely lives here:

* ``operation_class``  - read vs write. Write tools can never run without a
  matching human approval, no matter what the model asks for.
* ``risk_level``       - surfaced to the approver and the audit record.
* ``required_permissions`` - checked at planning time *and* again immediately
  before execution, so a revocation between the two fails closed.
* ``tenant_scoped``    - tools may only ever see their own tenant's data.
* ``requires_approval``- explicit per tool, defaulted from the operation class.
* ``timeout_seconds``  - per-step execution budget.
* ``idempotent``       - whether a replay may reuse a prior result.
* ``reversible``       - whether the runtime can offer reconciliation/rollback.
* ``parameters``       - a strict argument schema: unknown arguments are
  rejected rather than passed through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable


class OperationClass(str, Enum):
    READ = "read"
    WRITE = "write"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RegistryError(Exception):
    """Raised for an unknown tool, a malformed spec, or a bad argument."""

    def __init__(self, message: str, *, code: str = "registry_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    type: type
    required: bool = True
    max_length: int | None = None

    def coerce(self, value: Any) -> Any:
        if self.type is str:
            if not isinstance(value, str):
                raise RegistryError(
                    f"Argument '{self.name}' must be a string", code="bad_argument_type"
                )
            if self.max_length is not None and len(value) > self.max_length:
                raise RegistryError(
                    f"Argument '{self.name}' exceeds {self.max_length} characters",
                    code="argument_too_long",
                )
            return value
        if self.type is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise RegistryError(
                    f"Argument '{self.name}' must be an integer", code="bad_argument_type"
                )
            return value
        if self.type is bool:
            if not isinstance(value, bool):
                raise RegistryError(
                    f"Argument '{self.name}' must be a boolean", code="bad_argument_type"
                )
            return value
        raise RegistryError(
            f"Argument '{self.name}' has an unsupported declared type",
            code="bad_parameter_spec",
        )


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    operation_class: OperationClass
    risk_level: RiskLevel
    required_permissions: frozenset[str]
    parameters: tuple[ParameterSpec, ...] = ()
    tenant_scoped: bool = True
    requires_approval: bool | None = None
    timeout_seconds: int = 20
    idempotent: bool = True
    reversible: bool = True

    @property
    def is_write(self) -> bool:
        return self.operation_class is OperationClass.WRITE

    @property
    def approval_required(self) -> bool:
        """Write tools require approval unless a spec explicitly says otherwise."""
        if self.requires_approval is None:
            return self.is_write
        return self.requires_approval

    def validate_arguments(self, arguments: dict) -> dict:
        """Reject unknown or malformed arguments instead of forwarding them."""
        if not isinstance(arguments, dict):
            raise RegistryError("Tool arguments must be an object", code="bad_arguments")
        known = {p.name for p in self.parameters}
        unknown = set(arguments) - known
        if unknown:
            raise RegistryError(
                f"Unknown argument(s) for tool '{self.name}': {sorted(unknown)}",
                code="unknown_argument",
            )
        cleaned: dict[str, Any] = {}
        for spec in self.parameters:
            if spec.name not in arguments:
                if spec.required:
                    raise RegistryError(
                        f"Missing required argument '{spec.name}' for tool '{self.name}'",
                        code="missing_argument",
                    )
                continue
            cleaned[spec.name] = spec.coerce(arguments[spec.name])
        return cleaned

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "operation_class": self.operation_class.value,
            "risk_level": self.risk_level.value,
            "required_permissions": sorted(self.required_permissions),
            "parameters": [
                {"name": p.name, "type": p.type.__name__, "required": p.required}
                for p in self.parameters
            ],
            "tenant_scoped": self.tenant_scoped,
            "requires_approval": self.approval_required,
            "timeout_seconds": self.timeout_seconds,
            "idempotent": self.idempotent,
            "reversible": self.reversible,
        }


ToolHandler = Callable[[Any, dict], Awaitable[dict]]


@dataclass
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


@dataclass
class ToolRegistry:
    _tools: dict[str, RegisteredTool] = field(default_factory=dict)

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise RegistryError(f"Tool '{spec.name}' is already registered")
        if not spec.required_permissions:
            raise RegistryError(
                f"Tool '{spec.name}' must declare at least one required permission"
            )
        self._tools[spec.name] = RegisteredTool(spec=spec, handler=handler)

    def get(self, name: str) -> RegisteredTool:
        tool = self._tools.get(name)
        if tool is None:
            raise RegistryError(
                f"Tool '{name}' is not registered", code="unregistered_tool"
            )
        return tool

    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict]:
        return [t.spec.describe() for t in sorted(self._tools.values(), key=lambda t: t.spec.name)]
