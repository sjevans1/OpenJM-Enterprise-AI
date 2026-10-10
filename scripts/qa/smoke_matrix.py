#!/usr/bin/env python
"""QA1 identity/governance smoke matrix + representative governed retrieval.

Runs against the live QA1 acceptance environment (real Keycloak OIDC, real
PostgreSQL, real model gateway, real vector/RAG). Every check is a real HTTP
request; nothing is simulated. Prints a table and writes
$OPENJM_QA_ENV_ROOT/artifacts/smoke_matrix.json.

    source deploy/qa/qa_env.sh
    "$OPENJM_QA_ENV_ROOT/venv/bin/python" scripts/qa/smoke_matrix.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_env_lib as lib  # noqa: E402

RESULTS: list[dict] = []


def check(cid: str, description: str, passed: bool, detail: str = "") -> None:
    RESULTS.append({"id": cid, "check": description, "status": "PASS" if passed else "FAIL",
                    "detail": detail})
    print(f"[{'PASS' if passed else 'FAIL'}] {cid} {description}"
          + (f" :: {detail}" if detail else ""))


def main() -> int:
    credentials = json.loads((lib.secrets_dir() / "qa_credentials.json").read_text())
    ledger = lib.load_ledger()
    tenants = ledger["tenants"]
    principals = ledger["principals"]
    documents = ledger["documents"]

    def login(username: str) -> lib.OpenJM:
        return lib.OpenJM.login(username, credentials[username])

    # --- authentication -----------------------------------------------------
    sessions: dict[str, lib.OpenJM] = {}
    for cid, username in (
        ("C-01", "qa.employee"), ("C-02", "qa.steward"), ("C-03", "qa.clientadmin"),
        ("C-04", "qa.platform"), ("C-05", "qa.other"),
    ):
        try:
            sessions[username] = login(username)
            check(cid, f"{username} authenticates via real OIDC authorization-code + PKCE", True,
                  f"principal {sessions[username].principal['principal_id'][:12]}")
        except Exception as exc:  # noqa: BLE001
            check(cid, f"{username} authenticates", False, f"{type(exc).__name__}: {exc}")
    if len(sessions) != 5:
        print("aborting: not all personas authenticated")
        _write()
        return 1

    employee = sessions["qa.employee"]
    steward = sessions["qa.steward"]
    admin = sessions["qa.clientadmin"]
    platform = sessions["qa.platform"]
    other = sessions["qa.other"]

    # --- identity resolution / capabilities ---------------------------------
    ep = employee.principal
    check("C-06", "end user resolves to tenant A with ordinary member role",
          ep["tenant_id"] == tenants["acme"]["id"] and ep["role"] == "viewer",
          f"tenant={ep['tenant_id']} role={ep['role']}")

    sp = steward.principal
    scopes = {(s["scope_type"], s["scope_id"]) for s in sp["steward_scopes"]}
    check("C-07", "data steward resolves to tenant A with delegated HR stewardship",
          sp["tenant_id"] == tenants["acme"]["id"] and sp["role"] == "editor"
          and ("department", ledger["departments"]["hr"]["id"]) in scopes,
          f"role={sp['role']} steward_scopes={sorted(scopes)}")

    ap = admin.principal
    check("C-08", "client admin resolves to tenant A with tenant administration",
          ap["tenant_id"] == tenants["acme"]["id"] and "tenant:admin" in ap["permissions"]
          and ap["platform_capabilities"] == [],
          f"role={ap['role']} has tenant:admin={'tenant:admin' in ap['permissions']} "
          f"platform_caps={ap['platform_capabilities']}")

    pp = platform.principal
    expected_caps = ["platform:metadata:read", "platform:operators:admin", "platform:tenants:admin"]
    check("C-09", "platform operator resolves to the platform tenant with exactly the bootstrap "
                  "capabilities (no content support)",
          pp["tenant_id"] == tenants["openjm-platform"]["id"]
          and sorted(pp["platform_capabilities"]) == expected_caps,
          f"tenant={pp['tenant_id']} caps={sorted(pp['platform_capabilities'])}")

    op = other.principal
    check("C-10", "second-tenant user resolves to tenant B",
          op["tenant_id"] == tenants["globex"]["id"] and op["role"] == "owner",
          f"tenant={op['tenant_id']} role={op['role']}")

    # --- authorization boundaries -------------------------------------------
    resp = admin.get("/api/platform/status")
    check("C-11", "client admin receives no platform capability (platform control plane refused)",
          resp.status_code == 403, f"GET /api/platform/status -> {resp.status_code}")

    resp = platform.get("/api/auth/me", tenant=tenants["acme"]["id"])
    denied_a = resp.status_code != 200
    resp2 = platform.get("/api/knowledge/documents", tenant=tenants["acme"]["id"])
    check("C-12", "platform operator has no implicit customer-content access (no tenant A membership)",
          denied_a and resp2.status_code != 200,
          f"auth/me tenant=A -> {resp.status_code}; knowledge tenant=A -> {resp2.status_code}")

    resp = employee.get("/api/admin/members")
    check("C-13", "ordinary employee has no tenant administration",
          resp.status_code == 403 and employee.principal["permissions"].count("tenant:admin") == 0,
          f"GET /api/admin/members -> {resp.status_code}")

    resp = steward.get("/api/admin/members")
    check("C-14", "data steward is not a tenant administrator",
          resp.status_code == 403, f"GET /api/admin/members -> {resp.status_code}")

    resp = employee.get("/api/knowledge/documents")
    acme_docs = {d["id"] for d in resp.json()} if resp.status_code == 200 else set()
    resp2 = other.get("/api/knowledge/documents")
    globex_docs = {d["id"] for d in resp2.json()} if resp2.status_code == 200 else set()
    overlap = acme_docs & globex_docs
    handbook_id = documents["employee_handbook"]["id"]
    check("C-15", "second tenant remains isolated (no shared governed documents)",
          not overlap, f"acme_docs={len(acme_docs)} globex_docs={len(globex_docs)} overlap={len(overlap)}")

    # cross-tenant ownership probe: tenant B asking for a tenant A governed
    # source must be indistinguishable from "not found".
    source_id = ledger["data_sources"]["qa_demo"]["id"]
    probe = other.get(f"/api/data/sources/{source_id}")
    check("C-16", "cross-tenant governed-source access is refused (ownership scoped)",
          probe.status_code in (403, 404),
          f"tenant B GET /api/data/sources/<tenant A source> -> {probe.status_code}")

    # --- no dev-auth fallback ----------------------------------------------
    anon = httpx.get(f"{lib.BACKEND}/api/auth/me", timeout=10.0)
    cfg = httpx.get(f"{lib.BACKEND}/api/auth/config", timeout=10.0).json()
    check("C-17", "no dev-auth fallback is active (anonymous request refused; auth_mode=oidc)",
          anon.status_code == 401 and cfg.get("auth_mode") == "oidc"
          and cfg.get("oidc_configured") is True,
          f"anon /api/auth/me -> {anon.status_code}; auth_mode={cfg.get('auth_mode')}")

    # --- representative governed retrieval ----------------------------------
    def knowledge_query(client: lib.OpenJM, message: str) -> list[dict]:
        try:
            out = client.chat_slow(message, mode="knowledge")
        except httpx.HTTPError as exc:
            print(f"[warn] knowledge query failed: {type(exc).__name__}: {exc}")
            return []
        if out["status_code"] != 200:
            print(f"[warn] knowledge query status {out['status_code']}: {str(out['body'])[:200]}")
            return []
        return out["body"].get("evidence", [])

    ev = knowledge_query(employee, "What does the employee handbook say about probation and paid leave?")
    srcs = {e.get("source_id") for e in ev}
    check("C-18", "ordinary member retrieval returns authorized tenant-wide evidence",
          handbook_id in srcs and len(ev) > 0,
          f"evidence={len(ev)} sources={sorted(s for s in srcs if s)[:3]}")

    check("C-19", "ordinary member retrieval does NOT return the restricted HR document",
          documents["hr_compensation_policy"]["id"] not in srcs,
          f"hr_doc_returned={documents['hr_compensation_policy']['id'] in srcs}")

    ev_s = knowledge_query(steward, "What is the FY2025 discretionary bonus target for the HR band?")
    srcs_s = {e.get("source_id") for e in ev_s}
    check("C-20", "data steward retrieval returns the restricted HR evidence they are authorized for",
          documents["hr_compensation_policy"]["id"] in srcs_s,
          f"evidence={len(ev_s)} hr_doc_returned="
          f"{documents['hr_compensation_policy']['id'] in srcs_s}")

    # structured data (best effort: the planner must select the governed source)
    try:
        out = steward.chat_slow("How many orders are there in total?", mode="data", timeout=300.0)
        structured_ok = out["status_code"] == 200 and any(
            e.get("source_type") == "structured_query" for e in out["body"].get("evidence", [])
        )
        detail = (f"status={out['status_code']} "
                  f"evidence_types="
                  f"{[e.get('source_type') for e in out['body'].get('evidence', [])][:3]}")
    except httpx.HTTPError as exc:
        structured_ok = False
        detail = f"{type(exc).__name__}: {exc}"
    check("C-21", "governed structured retrieval executes against the PostgreSQL source",
          structured_ok, detail)

    # --- readiness detail (ops token) --------------------------------------
    ops = lib.read_secret("ops_token.txt")
    detail = httpx.get(f"{lib.BACKEND}/api/ready/detail",
                       headers={"Authorization": f"Bearer {ops}"}, timeout=20.0)
    components = detail.json().get("components", {}) if detail.status_code == 200 else {}
    check("C-22", "backend readiness detail green with PostgreSQL + model provider components",
          detail.status_code == 200 and detail.json().get("ready") is True,
          f"ready={detail.json().get('ready') if detail.status_code == 200 else detail.status_code} "
          f"components={sorted(components)}")

    model_comp = components.get("model_provider", {})
    check("C-23", "approved model-gateway path is reachable and configured",
          bool(model_comp) and model_comp.get("status") == "ok",
          f"model_provider={ {k: model_comp.get(k) for k in ('mode', 'model', 'status')} }")

    # --- no Rahkia cutover claim -------------------------------------------
    version = httpx.get(f"{lib.BACKEND}/api/version", timeout=10.0).json()
    public = httpx.get(f"{lib.BACKEND}/api/config/public", timeout=10.0).json()
    blob = json.dumps({**version, **public}).lower()
    frontend = httpx.get(f"{lib.FRONTEND}/", timeout=10.0)
    check("C-24", "no Rahkia production cutover is claimed by the running build",
          "rahkia" not in blob and version.get("profile") == "production",
          f"profile={version.get('profile')} frontend={frontend.status_code}")

    _write()
    failed = [r for r in RESULTS if r["status"] == "FAIL"]
    print(f"\n== smoke matrix: {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS ==")
    return 1 if failed else 0


def _write() -> None:
    path = lib.artifacts_dir() / "smoke_matrix.json"
    path.write_text(json.dumps(
        {"checks": RESULTS,
         "summary": {"total": len(RESULTS),
                     "pass": sum(1 for r in RESULTS if r["status"] == "PASS"),
                     "fail": sum(1 for r in RESULTS if r["status"] == "FAIL")}},
        indent=2, sort_keys=True))
    print("[smoke] report written:", path)


if __name__ == "__main__":
    raise SystemExit(main())
