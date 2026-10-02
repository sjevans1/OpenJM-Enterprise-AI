"""VS4-B2B immutable source-scope boundary for governed report execution.

A scope is always server-owned and must originate from a validated immutable
ReportDefinitionVersion. It narrows, never enlarges, current authorization.
No endpoint in B2B invokes scoped execution.
"""
import json
from dataclasses import dataclass

from app.models import DataSource
from app.services.data_sources import decode_schema
from typing import Mapping


class ReportScopeError(ValueError):
    """Missing or malformed pinned source scope; refuse instead of widening."""


@dataclass(frozen=True)
class ReportSourceScope:
    document_ids: frozenset[str]
    source_tables: tuple[tuple[str, frozenset[str]], ...]

    @classmethod
    def from_pins(
        cls, document_ids: list[str], source_tables: Mapping[str, list[str]]
    ) -> "ReportSourceScope":
        if (
            not isinstance(document_ids, list)
            or len(document_ids) > 32
            or any(not isinstance(x, str) or not x.strip() or len(x) > 128 for x in document_ids)
            or len(document_ids) != len(set(document_ids))
            or not isinstance(source_tables, dict)
            or len(source_tables) > 8
        ):
            raise ReportScopeError("Invalid pinned document/source identifiers")
        if any(not isinstance(identifier, str) for identifier in source_tables):
            raise ReportScopeError("Invalid structured source identifiers")
        normalized: list[tuple[str, frozenset[str]]] = []
        count = 0
        for identifier, tables in sorted(source_tables.items()):
            if (
                not isinstance(identifier, str)
                or not identifier.strip()
                or len(identifier) > 128
                or not isinstance(tables, list)
                or not 1 <= len(tables) <= 32
                or any(not isinstance(t, str) or not t.strip() or len(t) > 255 for t in tables)
            ):
                raise ReportScopeError("Invalid pinned structured source")
            table_set = frozenset(t.strip().casefold() for t in tables)
            if len(table_set) != len(tables):
                raise ReportScopeError("Duplicate pinned table")
            normalized.append((identifier, table_set))
            count += len(tables)
        if count > 32 or (not document_ids and not source_tables):
            raise ReportScopeError("Empty or excessive pinned scope")
        return cls(frozenset(document_ids), tuple(normalized))

    def tables_for(self, source_id: str) -> frozenset[str]:
        for identifier, tables in self.source_tables:
            if identifier == source_id:
                return tables
        raise ReportScopeError("Structured source outside report definition")

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(identifier for identifier, _ in self.source_tables)

    def require_documents(self) -> frozenset[str]:
        if not self.document_ids:
            raise ReportScopeError("No pinned documents for Knowledge path")
        return self.document_ids

    def require_sources(self) -> frozenset[str]:
        if not self.source_tables:
            raise ReportScopeError("No pinned structured sources for Data path")
        return self.source_ids


def source_scope_still_authorized(
    source: DataSource, pinned_tables: frozenset[str]
) -> bool:
    """Fail closed on stale schema, disabled sources, or missing explicit grants."""
    if (
        not pinned_tables
        or not source.enabled
        or source.status != "connected"
        or not source.schema_json
        or not source.authorized_objects_json
    ):
        return False
    try:
        authorized = json.loads(source.authorized_objects_json)
    except (ValueError, TypeError):
        return False
    if (
        not isinstance(authorized, list)
        or any(not isinstance(item, str) for item in authorized)
    ):
        return False
    granted = {item.strip().casefold() for item in authorized if item.strip()}
    if not pinned_tables.issubset(granted):
        return False

    # An unqualified table name shared by multiple discovered schemas is not
    # a unique capability. Never let a pin for "finance" also cover
    # "private.finance" simply because the final table name matches.
    discovered_tables = decode_schema(source.schema_json)
    for pin in pinned_tables:
        matches = [
            table for table in discovered_tables
            if pin in {table.name.casefold(), table.qualified_name.casefold()}
        ]
        if len(matches) != 1:
            return False
        # PostgreSQL can resolve an unqualified name through search_path.
        # Scoped reports require the explicitly schema-qualified pin.
        if source.engine == "postgresql" and (
            "." not in pin or pin != matches[0].qualified_name.casefold()
        ):
            return False
        if "." in pin and pin != matches[0].qualified_name.casefold():
            return False
    return True
