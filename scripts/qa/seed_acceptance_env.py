#!/usr/bin/env python
"""Seed the OpenJM Enterprise AI QA1 acceptance environment.

Deterministic, non-sensitive fixtures only. Two mechanisms, chosen deliberately:

* The one thing a pure-HTTP seeder cannot do is give the FIRST platform operator
  its opening membership: bootstrap has no HTTP endpoint (CLI/service only) and
  the operator cannot authenticate until it has an active membership. That single
  step runs through the application service layer in-process.
* Everything else is driven over the product's own HTTP API using real Keycloak
  OIDC authorization-code + PKCE logins, so the admin/platform/knowledge/data
  paths are exercised as a real client would.

The seeding is idempotent: it reuses an existing department/group/member/document/
source of the same name instead of duplicating it, so it can be re-run.

Preconditions: PostgreSQL + Keycloak are up (deploy/qa/docker-compose.qa.yml) and
the backend is serving (scripts/qa/start_backend.sh).

    source deploy/qa/qa_env.sh
    "$OPENJM_QA_ENV_ROOT/venv/bin/python" scripts/qa/seed_acceptance_env.py --phase all
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_env_lib as lib  # noqa: E402

TENANT_A_SLUG, TENANT_A_NAME = "acme", "Acme Corporation"
TENANT_B_SLUG, TENANT_B_NAME = "globex", "Globex Industries"
TENANT_P_SLUG, TENANT_P_NAME = "openjm-platform", "OpenJM Platform Operations"
QA_DEMO_DSN = "postgresql://openjm:{pw}@127.0.0.1:15432/qa_demo"
QA_OPS_DSN = "postgresql://openjm:{pw}@127.0.0.1:15432/qa_ops_demo"
SQL_DIR = Path(__file__).resolve().parents[2] / "deploy" / "qa" / "sql"


def ensure_demo_database(database: str, sql_file: str) -> None:
    """Create a synthetic fixture database and apply its schema/data."""
    import psycopg

    pw = lib.read_secret("db_password.txt")
    with psycopg.connect(f"postgresql://openjm:{pw}@127.0.0.1:15432/postgres",
                         autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{database}"')
                print(f"[seed] created fixture database {database}")
    with psycopg.connect(f"postgresql://openjm:{pw}@127.0.0.1:15432/{database}") as conn:
        conn.execute((SQL_DIR / sql_file).read_text())
        conn.commit()
    print(f"[seed] applied {sql_file} to {database}")


def ok(response: httpx.Response, *codes: int, ctx: str = "") -> httpx.Response:
    if response.status_code not in (codes or (200, 201, 204)):
        raise SystemExit(
            f"[seed] {ctx}: unexpected HTTP {response.status_code}: {response.text[:400]}"
        )
    return response


# --------------------------------------------------------------------------
# Phase A: Keycloak realm provisioning (secret rotation + personas)
# --------------------------------------------------------------------------


def phase_a_keycloak() -> dict[str, str]:
    kc = lib.KeycloakAdmin()
    if not kc.wait_ready():
        raise SystemExit("[seed] Keycloak realm 'openjm' did not become ready")
    print("[seed] keycloak realm ready:", lib.REALM)
    secret = kc.rotate_client_secret()
    lib.write_secret("openjm_client_secret.txt", secret)
    print("[seed] rotated client secret -> secrets/openjm_client_secret.txt")

    passwords: dict[str, str] = {}
    for username in lib.PERSONAS:
        password = secrets.token_urlsafe(18)
        kc.set_password(username, password)
        passwords[username] = password
    lib.write_secret("qa_credentials.json", json.dumps(passwords))
    print("[seed] set persona passwords -> secrets/qa_credentials.json")

    subs = {username: kc.get_user(username)["id"] for username in lib.PERSONAS}
    print("[seed] resolved OIDC subjects:", ", ".join(sorted(subs)))
    return subs


# --------------------------------------------------------------------------
# Phase B: platform trust root + operator's opening membership (service layer)
# --------------------------------------------------------------------------


async def phase_b_platform_trust_root(subs: dict[str, str]) -> str:
    from sqlalchemy import select

    from app.db import SessionLocal, init_db
    from app.models import Tenant
    from app.services import identity as identity_service
    from app.services import platform_bootstrap

    await init_db()
    operator_subject = subs["qa.platform"]
    async with SessionLocal() as db:
        try:
            result = await platform_bootstrap.bootstrap_first_operator(
                db,
                subject=operator_subject,
                email="qa.platform@qa.openjm.local",
                display_name="QA Platform Operator",
            )
            print("[seed] bootstrapped first platform operator:", result["principal_id"])
        except platform_bootstrap.BootstrapError as exc:
            print("[seed] bootstrap already established (idempotent):", exc)

        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == TENANT_P_SLUG))
        ).scalar_one_or_none()
        if tenant is None:
            tenant = await identity_service.create_tenant(
                db, slug=TENANT_P_SLUG, name=TENANT_P_NAME
            )
        account = await identity_service.get_or_create_principal(
            db,
            subject=operator_subject,
            email="qa.platform@qa.openjm.local",
            display_name="QA Platform Operator",
        )
        await identity_service.add_membership(
            db, tenant_id=tenant.id, principal_id=account.id, role="owner"
        )
        await db.commit()
        print("[seed] platform tenant + operator membership ready:", tenant.id)
        return tenant.id


# --------------------------------------------------------------------------
# Phase C: HTTP seeding with real OIDC sessions
# --------------------------------------------------------------------------


def wait_backend(timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{lib.BACKEND}/api/health", timeout=5.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    raise SystemExit("[seed] backend did not become healthy")


def _find(items: list[dict], **match) -> dict | None:
    return next((i for i in items if all(i.get(k) == v for k, v in match.items())), None)


def ensure_member(admin: lib.OpenJM, payload: dict) -> dict:
    resp = admin.post("/api/admin/members", payload)
    if resp.status_code in (200, 201):
        return resp.json()
    existing = _find(admin.get("/api/admin/members").json(), subject=payload["subject"])
    if existing is None:
        raise SystemExit(f"[seed] member {payload['subject']}: {resp.status_code} {resp.text[:200]}")
    return existing


def ensure_department(admin: lib.OpenJM, slug: str, name: str) -> dict:
    resp = admin.post("/api/admin/departments", {"slug": slug, "name": name})
    if resp.status_code in (200, 201):
        return resp.json()
    existing = _find(admin.get("/api/admin/departments").json(), slug=slug)
    if existing is None:
        raise SystemExit(f"[seed] department {slug}: {resp.status_code} {resp.text[:200]}")
    return existing


def ensure_group(admin: lib.OpenJM, slug: str, name: str, department_id: str | None) -> dict:
    payload = {"slug": slug, "name": name}
    if department_id:
        payload["department_id"] = department_id
    resp = admin.post("/api/admin/groups", payload)
    if resp.status_code in (200, 201):
        return resp.json()
    existing = _find(admin.get("/api/admin/groups").json(), slug=slug)
    if existing is None:
        raise SystemExit(f"[seed] group {slug}: {resp.status_code} {resp.text[:200]}")
    return existing


def ensure_group_member(admin: lib.OpenJM, group_id: str, principal_id: str) -> None:
    resp = admin.post(f"/api/admin/groups/{group_id}/members", {"principal_id": principal_id})
    if resp.status_code in (200, 201, 409):
        return
    raise SystemExit(f"[seed] group member: {resp.status_code} {resp.text[:200]}")


def ensure_steward(admin: lib.OpenJM, principal_id: str, scope_type: str, scope_id: str) -> dict:
    resp = admin.post(
        "/api/admin/stewards",
        {"principal_id": principal_id, "scope_type": scope_type, "scope_id": scope_id},
    )
    if resp.status_code in (200, 201):
        return resp.json()
    existing = _find(
        admin.get("/api/admin/stewards").json(),
        principal_id=principal_id, scope_type=scope_type, scope_id=scope_id,
    )
    if existing is None:
        raise SystemExit(f"[seed] steward grant: {resp.status_code} {resp.text[:200]}")
    return existing


def ensure_document(viewer: lib.OpenJM, uploader: lib.OpenJM, filename: str, text: str) -> dict:
    """Reuse an existing governed document of the same name, else upload it.

    ``viewer`` must be a principal that can actually see the fixture (visibility
    is classification/group scoped, so e.g. the Finance document is invisible to
    the HR steward); ``uploader`` performs the upload (needs knowledge:write).
    """
    existing = _find(viewer.get("/api/knowledge/documents").json(), original_name=filename)
    if existing is not None:
        return existing
    return uploader.upload_document(filename, text.encode(), "text/markdown")


def ensure_source(steward: lib.OpenJM, name: str, dsn: str) -> dict:
    existing = _find(steward.get("/api/data/sources").json(), name=name)
    if existing is not None:
        return existing
    resp = ok(
        steward.post("/api/data/sources",
                     {"name": name, "engine": "postgresql", "connection_uri": dsn}),
        200, 201, ctx="create structured source",
    )
    return resp.json()


def phase_c(subs: dict[str, str]) -> dict:
    credentials = json.loads((lib.secrets_dir() / "qa_credentials.json").read_text())
    ledger: dict = {"keycloak": {"realm": lib.REALM, "client_id": lib.CLIENT_ID},
                    "tenants": {}, "principals": {}, "departments": {}, "groups": {},
                    "documents": {}, "data_sources": {}}

    def login(username: str) -> lib.OpenJM:
        return lib.OpenJM.login(username, credentials[username])

    # --- platform operator: tenants + initial memberships -------------------
    platform = login("qa.platform")
    print("[seed] qa.platform capabilities:", platform.principal["platform_capabilities"])
    for slug, name in ((TENANT_A_SLUG, TENANT_A_NAME), (TENANT_B_SLUG, TENANT_B_NAME)):
        resp = ok(platform.post("/api/platform/tenants", {"slug": slug, "name": name}),
                  200, 201, ctx=f"create tenant {slug}")
        ledger["tenants"][slug] = resp.json()
    for slug, persona in ((TENANT_A_SLUG, "qa.clientadmin"), (TENANT_B_SLUG, "qa.other")):
        tenant_id = ledger["tenants"][slug]["id"]
        resp = ok(
            platform.post(f"/api/platform/tenants/{tenant_id}/memberships",
                          {"subject": subs[persona], "role": "owner",
                           "email": f"{persona}@qa.openjm.local"}),
            200, 201, ctx=f"provision {persona} owner in {slug}",
        )
        body = resp.json()
        ledger["principals"][persona] = {
            "subject": subs[persona], "principal_id": body["principal_id"], "home_tenant": slug,
        }
        print(f"[seed] {persona} -> owner of {slug}: {body['principal_id']}")

    # --- client admin: members, departments, groups, steward ----------------
    admin = login("qa.clientadmin")
    for persona, role in (("qa.employee", "viewer"), ("qa.steward", "editor")):
        body = ensure_member(admin, {"subject": subs[persona], "role": role,
                                     "email": f"{persona}@qa.openjm.local"})
        ledger["principals"][persona] = {
            "subject": subs[persona], "principal_id": body["principal_id"],
            "home_tenant": TENANT_A_SLUG,
        }
        print(f"[seed] {persona} -> {role} in acme: {body['principal_id']}")

    for slug, name in (("general", "General"), ("hr", "HR"), ("finance", "Finance")):
        ledger["departments"][slug] = ensure_department(admin, slug, name)
    for slug, name, dept in (
        ("all-employees", "All Employees", None),
        ("hr-leadership", "HR Leadership", "hr"),
        ("finance-leadership", "Finance Leadership", "finance"),
    ):
        ledger["groups"][slug] = ensure_group(
            admin, slug, name,
            ledger["departments"][dept]["id"] if dept else None,
        )
    print("[seed] departments/groups ready")

    for group_slug, persona in (
        ("all-employees", "qa.employee"), ("all-employees", "qa.steward"),
        ("all-employees", "qa.clientadmin"), ("hr-leadership", "qa.steward"),
        ("finance-leadership", "qa.clientadmin"),
    ):
        ensure_group_member(admin, ledger["groups"][group_slug]["id"],
                            ledger["principals"][persona]["principal_id"])
    ensure_steward(admin, ledger["principals"]["qa.steward"]["principal_id"],
                   "department", ledger["departments"]["hr"]["id"])
    print("[seed] group memberships + HR stewardship granted")

    # --- knowledge fixtures -------------------------------------------------
    steward = login("qa.steward")
    specs = {
        "employee_handbook": {
            "filename": "employee-handbook.md",
            "text": (
                "Employee Handbook\n\n"
                "The probation period is three months. Standard working hours are "
                "08:30 to 17:00. Employees receive fifteen days of paid leave each "
                "year, increasing by one day after every three years of service."
            ),
            "client": admin,
            "viewer": steward,
            "policy": {"classification": "internal", "tenant_visible": True},
        },
        "hr_compensation_policy": {
            "filename": "hr-compensation-policy.md",
            "text": (
                "HR Compensation Policy (HR Leadership only)\n\n"
                "For FY2025, the discretionary bonus target for the HR band is 25 "
                "percent of base salary. Merit increases for the HR band are capped "
                "at 4 percent in FY2025."
            ),
            "client": steward,  # HR department stewardship covers this document
            "viewer": steward,
            "policy": {"classification": "highly_restricted",
                       "department_id": ledger["departments"]["hr"]["id"],
                       "allowed_group_ids": [ledger["groups"]["hr-leadership"]["id"]]},
        },
        "finance_policy": {
            "filename": "finance-policy.md",
            "text": (
                "Finance Policy (Finance Leadership only)\n\n"
                "Invoices above 10000 must be approved by two members of Finance "
                "Leadership. The FY2025 capital-expenditure ceiling is 250000."
            ),
            "client": admin,  # tenant admin: steward scope does not cover Finance
            "viewer": admin,  # only Finance Leadership can see this fixture
            "policy": {"classification": "confidential",
                       "department_id": ledger["departments"]["finance"]["id"],
                       "allowed_group_ids": [ledger["groups"]["finance-leadership"]["id"]]},
        },
    }
    for key, spec in specs.items():
        spec["record"] = ensure_document(
            spec["viewer"], steward, spec["filename"], spec["text"]
        )

    # Wait for ingestion: embedding is lazy on first use in the vector engine.
    for key, spec in specs.items():
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            listing = steward.get("/api/knowledge/documents").json()
            match = _find(listing, id=spec["record"]["id"])
            if match and match.get("status") == "ready":
                break
            time.sleep(3.0)
        else:
            raise SystemExit(f"[seed] document {key} never became retrieval-ready")

    for key, spec in specs.items():
        ok(spec["client"].patch(
            f"/api/knowledge/documents/{spec['record']['id']}/policy", spec["policy"]
        ), 200, ctx=f"set policy for {key}")
        ledger["documents"][key] = {
            "id": spec["record"]["id"], "filename": spec["filename"],
            "classification": spec["policy"]["classification"],
            "department_id": spec["policy"].get("department_id"),
            "allowed_group_ids": spec["policy"].get("allowed_group_ids", []),
        }
    print("[seed] knowledge fixtures ready:", ", ".join(sorted(ledger["documents"])))

    # --- structured data sources -------------------------------------------
    # Two governed sources: a restricted HR one (negative authorization tests)
    # and an internal tenant-visible one (so an ordinary member has an
    # authorized structured source). Both are synthetic.
    dsn = QA_DEMO_DSN.format(pw=lib.read_secret("db_password.txt"))
    ensure_demo_database("qa_demo", "qa_demo.sql")
    source = ensure_source(steward, "QA Demo Warehouse", dsn)
    ok(steward.post(f"/api/data/sources/{source['id']}/test"), 200, ctx="test source")
    ok(steward.post(f"/api/data/sources/{source['id']}/refresh"), 200, ctx="refresh source")
    ok(steward.patch(f"/api/data/sources/{source['id']}/policy",
                     {"classification": "highly_restricted",
                      "department_id": ledger["departments"]["hr"]["id"],
                      "allowed_group_ids": [ledger["groups"]["hr-leadership"]["id"]]}),
       200, ctx="set source policy")
    ledger["data_sources"]["qa_demo"] = {
        "id": source["id"], "name": source["name"], "engine": source["engine"],
        "classification": "highly_restricted",
    }
    print("[seed] restricted structured source ready:", source["id"])

    ensure_demo_database("qa_ops_demo", "qa_ops.sql")
    ops = ensure_source(steward, "QA Operations Warehouse",
                        QA_OPS_DSN.format(pw=lib.read_secret("db_password.txt")))
    ok(steward.post(f"/api/data/sources/{ops['id']}/test"), 200, ctx="test ops source")
    ok(steward.post(f"/api/data/sources/{ops['id']}/refresh"), 200, ctx="refresh ops source")
    # A department-less source is outside a department steward's scope (fail
    # closed), so the tenant admin applies the tenant-wide internal policy.
    ok(admin.patch(f"/api/data/sources/{ops['id']}/policy",
                   {"classification": "internal", "tenant_visible": True}),
       200, ctx="set ops source policy")
    ledger["data_sources"]["qa_ops"] = {
        "id": ops["id"], "name": ops["name"], "engine": ops["engine"],
        "classification": "internal", "tenant_visible": True,
    }
    print("[seed] tenant-visible structured source ready:", ops["id"])

    ledger["principals"]["qa.platform"] = {
        "subject": subs["qa.platform"],
        "principal_id": platform.principal["principal_id"],
        "home_tenant": TENANT_P_SLUG,
    }
    ledger["tenants"]["openjm-platform"] = {
        "id": platform.principal["tenant_id"], "slug": TENANT_P_SLUG, "name": TENANT_P_NAME,
    }
    return ledger


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Seed the OpenJM QA1 acceptance environment.")
    parser.add_argument(
        "--phase", choices=("keycloak", "openjm", "all"), default="all",
        help=("keycloak: realm secret rotation + persona passwords + subjects (no backend "
              "required); openjm: platform trust root + HTTP seeding (backend required); "
              "all: both, in order."),
    )
    args = parser.parse_args()

    subs: dict[str, str] | None = None
    if args.phase in {"keycloak", "all"}:
        print("== Phase A: Keycloak ==")
        subs = phase_a_keycloak()
    if args.phase == "keycloak":
        return 0

    if subs is None:
        kc = lib.KeycloakAdmin()
        if not kc.wait_ready():
            raise SystemExit("[seed] Keycloak realm 'openjm' did not become ready")
        subs = {username: kc.get_user(username)["id"] for username in lib.PERSONAS}

    print("== Phase B: platform trust root (service layer) ==")
    asyncio.run(phase_b_platform_trust_root(subs))
    print("== Phase C: HTTP seeding (real OIDC) ==")
    wait_backend()
    ledger = phase_c(subs)
    lib.save_ledger(ledger)
    print("[seed] ledger written:", lib.ledger_path())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
