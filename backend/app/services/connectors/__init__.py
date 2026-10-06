"""Connector package.

Public surface: the connector contract (:mod:`base`), the registry of declared
connector types (:mod:`app.core.connectors`), instance lifecycle and the
credential boundary (:mod:`service`), ingestion and quarantine (:mod:`ingest`),
current-authorization enforcement (:mod:`authorization`), and the sync and
reconciliation engine (:mod:`sync`).

Provider implementations live beside these modules and register themselves
explicitly. Nothing here performs a network call except through a registered
connector implementation.
"""

from __future__ import annotations

__all__ = [
    "authorization",
    "base",
    "ingest",
    "service",
    "sync",
]
