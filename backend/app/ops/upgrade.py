"""VS8 upgrade preflight/postflight and rollback strategy (Workstream G).

The supported upgrade path is deliberately narrow and honest:

1. **Preflight** — refuse an unknown/future schema revision, report the current
   schema and application version, and require a backup recommendation to be
   acknowledged before an in-place upgrade of a populated database.
2. **Upgrade** — the additive migration runner (:func:`app.db.init_db`
   -> ``adopt_and_upgrade``). Revisions are guarded per object so an in-place
   deployment that already has some objects is upgraded rather than aborted.
3. **Postflight** — confirm the recorded revision equals the build head and that
   identity/tenant state is intact.

**Rollback** is explicit and bounded. Because a destructive migration cannot be
reversed safely, the documented strategy is: back up first, roll the application
back only while the schema stays compatible, and otherwise restore from the
pre-upgrade backup. This module never claims a schema downgrade is safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings, get_settings
from app.migrations_runner import (
    UnknownSchemaRevisionError,
    assert_known_schema_revision,
    current_revision,
    script_heads,
)
from app.version import PRODUCT_VERSION


@dataclass
class UpgradeReport:
    database_url: str
    app_version: str
    schema_before: str | None
    schema_head: str | None
    adopted_baseline: bool = False
    created_baseline_tables: list[str] = field(default_factory=list)
    postflight_revision: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [
            f"application version: {self.app_version}",
            f"schema before: {self.schema_before}",
            f"schema head:   {self.schema_head}",
            f"schema after:  {self.postflight_revision}",
        ]
        if self.adopted_baseline:
            lines.append(
                "adopted pre-migration database at baseline"
                + (f" (created: {self.created_baseline_tables})" if self.created_baseline_tables else "")
            )
        for error in self.errors:
            lines.append(f"ERROR: {error}")
        lines.append("RESULT: " + ("OK" if self.ok else "STOPPED"))
        return "\n".join(lines)


def preflight_upgrade(settings: Settings | None = None) -> UpgradeReport:
    """Assess the current schema and refuse an unknown/future revision."""
    cfg = settings or get_settings()
    database_url = cfg.database_url
    report = UpgradeReport(
        database_url=database_url,
        app_version=PRODUCT_VERSION,
        schema_before=current_revision(database_url),
        schema_head=(script_heads(database_url) or [None])[0],
    )
    try:
        assert_known_schema_revision(database_url)
    except UnknownSchemaRevisionError as exc:
        report.errors.append(str(exc))
    return report


def run_upgrade(settings: Settings | None = None) -> UpgradeReport:
    """Preflight, apply additive migrations, then postflight.

    This is the callable form of the supported upgrade. ``db.init_db`` performs
    the same migration at startup; this wrapper exists for the operator command
    and the acceptance harness, and adds the postflight check.
    """
    from app.migrations_runner import adopt_and_upgrade

    cfg = settings or get_settings()
    report = preflight_upgrade(cfg)
    if not report.ok:
        return report

    result = adopt_and_upgrade(cfg.database_url)
    report.adopted_baseline = bool(result.get("adopted_baseline"))
    report.created_baseline_tables = list(result.get("created_baseline_tables") or [])
    report.postflight_revision = current_revision(cfg.database_url)

    head = report.schema_head
    if report.postflight_revision not in {head, *script_heads(cfg.database_url)}:
        report.errors.append(
            f"postflight revision {report.postflight_revision} does not match head {head}"
        )
    return report


def backup_recommendation(backup_dir: Path | None = None) -> str:
    """The operator-facing pre-upgrade instruction (no silent destructive path)."""
    location = backup_dir or get_settings().backup_dir
    return (
        "Before upgrading a populated deployment, take a backup: "
        f"`python scripts/openjm_ops.py backup --dest {location}/pre-upgrade`. "
        "Rollback from an incompatible schema is by restore, not by downgrade."
    )


__all__ = [
    "UpgradeReport",
    "backup_recommendation",
    "preflight_upgrade",
    "run_upgrade",
]
