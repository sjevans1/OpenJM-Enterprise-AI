# ZTK1 — zero-tribal-knowledge handoff acceptance

The final handoff evidence package for OpenJM Enterprise AI.

**The question this answers:** can a technically competent operator who has never
worked on this product take the repository and its published documentation and
successfully install, configure, operate, recover, troubleshoot and hand off the
product without undocumented maintainer knowledge?

**The question this does not answer:** whether a maintainer can reconstruct how
the product was built from prior session history. The examination deliberately
withheld that.

| Field | Value |
| --- | --- |
| Issue | #104 (umbrella #45) |
| Attempt-1 examination baseline | `9db58d49b16da54f5a0c4f41cded97a038fd6a0e` |
| Focused confirmation candidate | `e4a0999cad7a71c3e4b5c89b70b2ae103147e7fe` |
| Documentation integration base | `6243f485ca535df9f619559ff1e03ba15e1e9142` |
| Result | **PASS by evidence aggregation** |

## Method, and what was withheld

Two genuinely isolated operators were used. Each ran as a fresh subagent in its
own context and received **only**: the repository URL, the exact candidate SHA,
Issue #104 as its specification, a fresh disposable cloud sandbox, and the
sandbox access coordinates. Neither received this project's chat history, prior
REL1-B4 commands or scripts, earlier Keycloak or `.env` work, persona-seeding
scripts, Caddy/browser harness logic, or any controller-side knowledge of where
the documentation was weak. The controller (the maintainer-facing session)
observed but did not coach, and did not repair the environment mid-attempt.

## Attempt 1 — full independent lifecycle examination

Independent operator, fresh Docker Agentic Platform Cloud consumer (Ubuntu 26.04.1,
8 vCPU / 15 GiB, clean-host proof recorded before handover), repository cloned at
`9db58d49` and verified with `git rev-parse HEAD`. It built CPython 3.11.9 from
source and independently confirmed the published sha256
`e7de3240a8bc2b1e1ba5c81bf943f06861ff494b69fda990ce2722a504c6153d` against the
repository's own documented digest, then stood up PostgreSQL 16.15 and Keycloak
26.7.4 from the repository's own deployment assets.

| Z | Result | Note |
| --- | --- | --- |
| Z1 orientation | PASS | Product, profile, install/config paths, ops commands, backup/restore and upgrade/rollback model all discoverable |
| Z2 clean install | PARTIAL | Development profile exit 0 with REL1-A verifier OK; **production profile from a plain clone fails** because it needs the assembled release wheel (documentation gap G1) |
| Z3 production configuration | PASS | Preflight `VALID`; eight negative configurations all fail closed |
| Z4 real PostgreSQL 16 | PASS | 16.15, migration `0021 == head`, health/ready/version 200 |
| Z5 real OIDC | PASS | Five personas, real authorization-code + PKCE S256 |
| Z6 platform bootstrap | PASS | One-shot bootstrap, refuses replay, no content-support capability (G2) |
| Z7 identity/governance | PASS | Two tenants, roles, departments, groups, memberships, steward scope via supported API |
| Z8 model path | PASS | Local provider mode, fallback none; caveat: the local stub cannot plan SQL |
| Z9 knowledge | PASS | Ingest to `ready`, authorized retrieval, unauthorized negative held |
| Z10 structured data | PARTIAL | Registration, discovery, policy PASS; governed query not demonstrated with the incapable stub |
| Z11 chat/knowledge/data/hybrid | PASS | All four modes, correct execution classes |
| Z12 chat artifact | PASS | Persisted artifact, download 200, cross-tenant 404 |
| Z13 governed report | PASS | Save, reopen, rerun, provenance; cross-tenant 404 (G3) |
| Z14 usage/entitlements | PASS | Platform usage and entitlement metadata surfaces |
| Z15 backup | PASS | Manifest with per-file sha256 and byte counts; verify `ok:true` |
| Z16 restore | PASS | Independent clean target; identical row counts; original untouched |
| Z17 upgrade | PASS | `--check` OK, idempotent, unknown-future-revision fails closed |
| Z18 rollback/recovery | PASS | Documented backup-restore recovery; no false `downgrade` claim |
| Z19 deliberate failure | PASS | Operator-chosen PostgreSQL outage, diagnosed and recovered (surfaced D1) |
| Z20 security sanity | PASS | Dev auth off, no wildcard CORS/host, no silent model fallback, no secret leakage, platform operator without customer content, cross-tenant fails closed (G5 doc mismatch) |
| Z21 operability exam | PASS | Eleven of thirteen answers CLEAR; two AMBIGUOUS (D1 diagnostic, Rahkia deferral wording) |

Smoke matrix: C-01–C-20, C-22, C-23 PASS; **C-21 FAIL** (structured retrieval, stub-limited).

## Product defect D1 — found, corrected, live-validated

Attempt 1 found a genuine product defect: under a real PostgreSQL outage,
`GET /api/ready/detail` (valid ops token, production profile) returned **HTTP 500**
instead of the documented per-component readiness report. Log root cause:
`sqlalchemy.exc.PendingRollbackError: Can't reconnect until invalid transaction is
rolled back`. The readiness service documents that dependency failures are
**reported, never raised**, and that callers always receive a stable shape; the
unguarded `scheduler`, `connectors` and `notifications` probes violated that.

Corrected under **Issue #105 / PR #106** (merged as
`6243f485ca535df9f619559ff1e03ba15e1e9142`, **#105 CLOSED**): every probe now runs
through a guarded wrapper that reports a bounded
`{"status": "error", "detail": "<ExceptionClassName>"}` and resets the session
after a DB-dependent failure, so one failed dependency cannot abort the report.
RED→GREEN regression added; focused 4/4, operator-console 12/12, readiness
selection 34 passed / 2 skipped; PR-tier CI green (no FULL).

Live validation against the corrected candidate, real `postgres:16`:
healthy `/api/health` 200, `/api/ready` 200, `/api/ready/detail` 200 with eight
components and `database=ok`; PostgreSQL stopped **without touching the backend** →
`/api/ready` 200 `{"ready":false,"status":"degraded"}` and `/api/ready/detail`
**200, no 500**, `ready=false`, complete component map, `database=error` with
`migrations`/`scheduler`/`connectors`/`notifications` all bounded, `storage=ok`;
no secret, URL or traceback in the bounded 579-byte body; repeated probe still
bounded; PostgreSQL restarted → recovered 200 with `ready=true` and `database=ok`
with `RestartCount=0`, i.e. **no backend restart**. A second outage cycle behaved
identically.

## Documentation repair

Repaired on branch `ztk1-final-handoff-acceptance` (documents only; no product
code, no migration, no feature change):

| Gap | Correction |
| --- | --- |
| G1 production install path | `docs/INSTALLATION.md` now separates the development/source install from the production **release-payload** install, states that a plain clone is not a release artifact, and records the expected `production install requires the bundled application wheel` failure |
| G2 platform bootstrap | `docs/INSTALLATION.md` gains operator-facing first-operator guidance: `python -m app.services.platform_bootstrap <oidc-subject>`, prerequisites, no default password, no HTTP route, no standing anonymous privilege |
| G3 report-run configuration | `docs/CONFIGURATION.md` documents `OPENJM_REPORT_RUNS_ENABLED` (default `false`), the resulting **424**, and when to enable it; the endpoint path was also corrected |
| G4 readiness diagnostics | `docs/OPERATIONS.md` documents health/ready/ready-detail semantics and the accepted outage behaviour (bounded component report, never 500, self-recovery) |
| G5 dev-mode wording | `docs/SECURITY_POSTURE.md` corrected: `auth_mode=oidc` is the enforcing control, the flag alone is inert, dev identity needs `auth_mode=dev` **and** the flag, and preflight warns rather than fails on the flag |

Stale handoff material reconciled in the same pass: `HANDOFF_READINESS.md`
(current position; #79 and #105 closed; only Rahkia deferred), `README.md` (now an
orientation page rather than a stale VS-era ledger), `docs/ACCEPTANCE_MATRIX.md`
(narrative updated, evidence boundary preserved), and `docs/handoff/REL1_FOUNDATION.md`
(implemented items no longer listed as proposed; version drift and air-gapped
installer gap marked resolved). `doctor` and `support-bundle` remain explicitly
**proposed, not implemented**; the operator CLI is exactly `backup`, `verify`,
`restore`, `upgrade`, `retention`, `config`.

## Focused independent confirmation — fresh operator

A **second, completely fresh** isolated operator (own context, own sandbox, no
Attempt-1 transcript and no correction rationale) verified only the affected
gates.

| Gate | Result | Evidence |
| --- | --- | --- |
| F1 documentation orientation | PASS | All eight required subjects discoverable from the repository root; toolkit matches `openjm_ops.py` |
| F2 production vs source install | PASS | Docs unambiguous about the release payload and invocation context; the documented failure text fired verbatim in a plain clone |
| F3 platform bootstrap discoverability | PASS | Operator-facing path found; live smoke granted exactly the three platform capabilities, no content support; replay refused |
| F4 report-run 424 config | PASS | Setting, default and consequence confirmed; code matches docs |
| F5 readiness under DB outage | PASS (live) | Real outage reproduced the accepted bounded behaviour and self-recovery with no backend restart |
| F6 `auth_allow_dev_mode` | PASS | Wording now matches code exactly |
| F7 structured data (the former partial) | PASS (focused) | Governed structured path proven with a deterministic SQL stub: foundation and chat acceptance harnesses passed, including preserved SQL provenance and fail-closed cases. Caveat: the stub is not a real LLM, so this proves the governed path, not model SQL quality |

**No product defects were found by the focused operator.** Its five documentation
findings (a dangling `docs/ZTK1_ACCEPTANCE.md` reference — now this document; a
stale `REL1_FOUNDATION.md`; an imprecise report-run endpoint path; a malformed
bearer line in `OPERATIONS.md`; and a duplicate list number in
`docs/ACCEPTANCE_MATRIX.md`) were corrected in this same pass.

## Acceptance rationale

ZTK1 is declared PASS by evidence aggregation rather than by a second full
lifecycle run:

- the unaffected Attempt-1 PASS evidence remains valid and was **intentionally not
  rerun** — that is acceptance design, not missing evidence;
- the one genuine product defect, D1, was corrected and independently
  live-qualified under #105;
- the documentation defects found by the independent examination were repaired;
- a fresh independent operator re-verified the affected gates F1–F7 and found the
  corrected documentation sufficient and no product defects;
- no unresolved product defect remains.

## Boundaries

- **Rahkia remains DEFERRED by design.** It was neither integrated nor
  qualification-tested here.
- No model substitution and no public inference provider were used.
- No application code was changed by this acceptance package.
- The REL1-B4 live production/browser qualification belongs to the frozen artifact
  `709ce47bf3ae1b7135446c376bcf312b25606b5c`; later commits do not inherit it.
- ZTK1 is a handoff qualification at its own candidate SHAs, not a REL1-B4
  restamping.
