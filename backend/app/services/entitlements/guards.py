"""ORM-level immutability guards for the M3 credit ledger.

The credit ledger is append-only. Three layers enforce that, and this module is
the second:

1. the service never exposes an update or delete path for a ledger row;
2. the SQLAlchemy ``before_update``/``before_delete`` events here refuse any
   attempt to mutate an already-persisted row through the ORM;
3. migration ``0016_m3_entitlements`` installs a database trigger (SQLite) that
   refuses the same mutation even through raw SQL.

A correction is a new ``adjustment`` row, never an edit of history.
"""

from __future__ import annotations

from sqlalchemy import event

from app.core.entitlements import LedgerImmutabilityError
from app.models import CreditLedgerEntry


def _refuse(*_args, **_kwargs) -> None:
    raise LedgerImmutabilityError(
        "Credit ledger entries are append-only; history is never rewritten."
    )


@event.listens_for(CreditLedgerEntry, "before_update")
def _forbid_update(*args, **kwargs) -> None:  # pragma: no cover - exercised via flush
    _refuse(*args, **kwargs)


@event.listens_for(CreditLedgerEntry, "before_delete")
def _forbid_delete(*args, **kwargs) -> None:  # pragma: no cover - exercised via flush
    _refuse(*args, **kwargs)
