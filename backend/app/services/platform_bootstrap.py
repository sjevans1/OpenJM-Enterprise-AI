"""One-time secure bootstrap for the first OpenJM platform operator (BV3-A).

There is no bootstrap HTTP endpoint and no default or stored password. Bootstrapping
is an explicit operator action on the deployment host:

    python -m app.services.platform_bootstrap <validated-oidc-subject>

It is idempotent in the safe direction: once any active operator holds
``OPERATORS_ADMIN`` (the platform trust root), every later bootstrap attempt is
refused rather than silently granting more privilege. Afterwards the operator
authenticates through normal OIDC or a session; the account row it creates carries
no credential of its own.

Local development is separate: with ``auth_mode='dev'`` the accepted
``ensure_local_identity`` provisions the local tenant/owner for tests and the dev
walkthrough. This command is the production-like path and never grants
``CONTENT_SUPPORT``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.platform import (
    PlatformCapability,
    normalize_platform_capabilities,
)
from app.models import PlatformOperator
from app.services import access_governance
from app.services import identity as identity_service

# The capabilities the first operator receives: enough to run the control plane
# and to grant further operators. Content support is deliberately excluded and
# must be granted explicitly, and audited, afterwards.
BOOTSTRAP_CAPABILITIES: tuple[PlatformCapability, ...] = (
    PlatformCapability.METADATA_READ,
    PlatformCapability.TENANTS_ADMIN,
    PlatformCapability.OPERATORS_ADMIN,
)


class BootstrapError(RuntimeError):
    """A bootstrap attempt that must be refused."""


async def platform_trust_root_established(db: AsyncSession) -> bool:
    """True when any active operator already holds ``OPERATORS_ADMIN``."""
    row = (
        await db.execute(
            select(PlatformOperator.id).where(
                PlatformOperator.capability == PlatformCapability.OPERATORS_ADMIN.value,
                PlatformOperator.status == "active",
            )
        )
    ).first()
    return row is not None


async def bootstrap_first_operator(
    db: AsyncSession,
    *,
    subject: str,
    issuer: str | None = None,
    email: str | None = None,
    display_name: str | None = None,
    capabilities=None,
) -> dict:
    """Establish the first platform operator exactly once.

    Refuses when the trust root already exists (replay/escalation guard) and
    never grants content support.
    """
    if not subject or not subject.strip():
        raise BootstrapError("A validated subject is required")

    if await platform_trust_root_established(db):
        raise BootstrapError(
            "Platform trust root already established; bootstrap cannot be replayed"
        )

    if capabilities is None:
        resolved = frozenset(BOOTSTRAP_CAPABILITIES)
    else:
        resolved = normalize_platform_capabilities(capabilities)
    if PlatformCapability.CONTENT_SUPPORT in resolved:
        raise BootstrapError(
            "Bootstrap never grants content support; grant it explicitly afterwards"
        )
    if not resolved:
        raise BootstrapError("At least one platform capability is required")

    account = await identity_service.get_or_create_principal(
        db, subject=subject, issuer=issuer, email=email, display_name=display_name
    )
    await access_governance.grant_platform_operator(
        db,
        principal_id=account.id,
        capabilities=resolved,
        granted_by="bootstrap",
    )
    await identity_service.record_audit(
        db,
        principal=None,
        action="platform.bootstrap",
        decision="allow",
        resource_type="platform_operator",
        resource_id=account.id,
        metadata={
            "subject": subject,
            "capabilities": sorted(c.value for c in resolved),
        },
    )
    await db.commit()
    return {
        "principal_id": account.id,
        "subject": subject,
        "capabilities": sorted(c.value for c in resolved),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-time OpenJM platform operator bootstrap (no HTTP endpoint)."
    )
    parser.add_argument("subject", help="A validated OIDC subject to provision")
    parser.add_argument("--issuer", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--display-name", default=None)
    args = parser.parse_args(argv)

    async def _run() -> dict:
        from app.db import SessionLocal, init_db

        await init_db()
        async with SessionLocal() as db:
            return await bootstrap_first_operator(
                db,
                subject=args.subject,
                issuer=args.issuer,
                email=args.email,
                display_name=args.display_name,
            )

    try:
        result = asyncio.run(_run())
    except BootstrapError as exc:
        print(f"bootstrap refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
