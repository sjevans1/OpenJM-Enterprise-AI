#!/usr/bin/env python
"""VS8 operator commands: backup, restore, verify, upgrade, retention, config.

Thin, testable wrappers over the ``app.ops`` and ``app.services`` libraries so
the same code path is exercised by the DR/upgrade acceptance harness and by an
operator on the command line.

Usage:
    python scripts/openjm_ops.py backup  --dest /backups/2026-01-01
    python scripts/openjm_ops.py verify  /backups/2026-01-01
    python scripts/openjm_ops.py restore /backups/2026-01-01 \
        --target-data-dir /srv/openjm/data --target-database-url \
        sqlite+aiosqlite:////srv/openjm/data/openjm.db --force
    python scripts/openjm_ops.py upgrade [--check]
    python scripts/openjm_ops.py retention [--dry-run] [--apply] [--tenant ID]
    python scripts/openjm_ops.py config
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow `python scripts/openjm_ops.py` from the repo root.
BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _cmd_backup(args) -> int:
    from app.ops.backup import create_backup

    dest = Path(args.dest) if args.dest else None
    target = create_backup(dest, include_vector=not args.skip_vector)
    print(json.dumps({"backup": str(target)}, indent=2))
    return 0


def _cmd_verify(args) -> int:
    from app.ops.backup import verify_backup

    result = verify_backup(Path(args.backup))
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


def _cmd_restore(args) -> int:
    from app.ops.backup import restore_backup

    result = restore_backup(
        Path(args.backup),
        target_database_url=args.target_database_url,
        target_data_dir=Path(args.target_data_dir),
        force=args.force,
    )
    print(json.dumps(result, indent=2))
    return 0


def _cmd_upgrade(args) -> int:
    from app.ops.upgrade import preflight_upgrade, run_upgrade

    report = preflight_upgrade() if args.check else run_upgrade()
    print(report.render())
    return 0 if report.ok else 1


def _cmd_retention(args) -> int:
    from app.db import SessionLocal
    from app.services.retention import apply_retention

    dry_run = not args.apply

    async def _run() -> dict:
        async with SessionLocal() as db:
            outcome = await apply_retention(
                db, tenant_id=args.tenant, dry_run=dry_run
            )
        return {"dry_run": outcome.dry_run, "counts": outcome.counts, "total": outcome.total}

    print(json.dumps(asyncio.run(_run()), indent=2))
    return 0


def _cmd_config(args) -> int:
    from app.core.preflight import validate_configuration
    from app.core.config import get_settings

    report = validate_configuration(get_settings())
    print(report.render())
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenJM operator commands")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("backup", help="create an application-consistent backup")
    p.add_argument("--dest", default=None)
    p.add_argument("--skip-vector", action="store_true")
    p.set_defaults(func=_cmd_backup)

    p = sub.add_parser("verify", help="verify a backup manifest and checksums")
    p.add_argument("backup")
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("restore", help="restore a backup into a clean target")
    p.add_argument("backup")
    p.add_argument("--target-data-dir", required=True)
    p.add_argument("--target-database-url", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cmd_restore)

    p = sub.add_parser("upgrade", help="run (or check) the supported upgrade")
    p.add_argument("--check", action="store_true")
    p.set_defaults(func=_cmd_upgrade)

    p = sub.add_parser("retention", help="plan or apply bounded retention")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--tenant", default=None)
    p.set_defaults(func=_cmd_retention)

    p = sub.add_parser("config", help="validate the configuration preflight")
    p.set_defaults(func=_cmd_config)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
