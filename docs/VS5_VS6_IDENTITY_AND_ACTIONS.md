# VS5 + VS6 — Identity, Tenancy, Migrations and Governed Actions

This document is the contract for the VS5 (trusted identity and tenant
isolation) and VS6 (bounded action runtime) release increment. It records the
decisions that other teams and operators need in order to integrate safely.

---

## 1. Identity contract

### 1.1 Who authenticates

Every protected request must present **one** of:

| Credential | Form | Validated by |
| --- | --- | --- |
| OIDC JWT | `Authorization: Bearer <jwt>` (three dot-separated segments) | `app/services/oidc.py` against the configured issuer's JWKS |
| OpenJM session | `Authorization: Bearer <opaque token>` | `app/services/identity.py` against the stored SHA-256 digest |

Anything else is refused. There is no anonymous principal and no default user.

### 1.2 What a token proves

A validated token proves **who** the caller is. It never carries roles or
permissions:

- the token contributes a `sub` and an optional tenant claim;
- the role comes from the `tenant_memberships` row, re-read on **every** request;
- the permission set is derived from that role in code (`app/core/permissions.py`).

Consequence: a role change, a membership revocation, an account disable or a
tenant suspension takes effect on the **next request**. There is no token, cache
or session copy that can outlive it. Revoking a membership also revokes every
session bound to it.

### 1.3 Tenancy

- Every tenant-owned row carries `tenant_id` (the isolation boundary).
- The ownership key written to the legacy `user_id` column is
  `<tenant_id>:<principal_id>` outside the local tenant, so an ownership
  predicate is tenant-correct by construction. The local development tenant
  keeps the bare principal id so pre-VS5 rows stay addressable.
- A caller may *request* a tenant with the header `X-OpenJM-Tenant`. It is
  honoured only against an active membership; it can narrow scope, never widen
  it. An ambiguous multi-tenant principal without a tenant claim is refused.
- A resource belonging to another tenant is reported as **not found**, so a
  cross-tenant probe cannot enumerate the other tenant's resources.

### 1.4 Authentication modes

| `OPENJM_AUTH_MODE` | Behaviour |
| --- | --- |
| `oidc` (production) | A credential is mandatory. Fail closed on anything missing, malformed, expired, ambiguous or revoked. |
| `dev` (local/test only) | Resolves the configured local principal, still as a real tenant + principal + role read from the database. Gated by `OPENJM_AUTH_ALLOW_DEV_MODE`; it is not a bypass and is unavailable in `oidc` mode. |

### 1.5 Roles

| Role | Adds |
| --- | --- |
| `viewer` | read chat, knowledge, data, reports, traces, actions |
| `editor` | + write knowledge/data/reports, run reports, export, plan and execute actions |
| `admin` | + delete knowledge, read audit, approve actions, tenant admin |
| `owner` | everything |

An unknown or missing role resolves to **no** permissions, never to a default.

### 1.6 Authorization is server-side

Every route depends on a `require(...)` guard. Authorization is never delegated
to the UI, and permission failures are recorded as audit `deny` decisions.

---

## 2. Workspace independence (#9)

OpenJM Enterprise AI is a separate product. The following are **tested**, not
merely documented (`backend/tests/test_product_independence.py`):

- no source imports from Workspace;
- no shared database, ORM models, session store, secrets or deployment;
- no runtime requirement for Workspace to be available;
- the application boots and serves with no Workspace environment present.

The Workspace connector is **not** part of this increment. It belongs to the
later connector phase; when it lands it must consume the §1 contract as an
external client, not by sharing state.

---

## 3. Application-metadata migrations (#7)

### 3.1 Scope

Migrations apply **only** to OpenJM's own application-metadata database
(conversations, messages, documents, data sources, traces, reports, definitions,
run history, and the VS5/VS6 tables).

They never touch a customer's connected structured data source. Connected data
remains governed separately by the read-only SQL and credential-vault controls
from VS2.

### 3.2 Revision chain

| Revision | Contents |
| --- | --- |
| `0001_vs4_baseline` | The accepted VS1–VS4 schema, exactly as it existed when the framework was introduced |
| `0002_vs5_identity` | Tenants, principals, memberships, sessions, audit; `tenant_id` on every owned table; local tenant adopted |
| `0003_document_lifecycle` | Lifecycle state, version, ingest token, indexed/deleted timestamps; existing rows classified deterministically |
| `0004_action_runtime` | Plans, approvals, executions |
| `0005_document_leases` | Cross-process document leases |
| `0006_guard_triggers` | Immutability guards for run history and document resurrection (per dialect) |

### 3.3 Upgrading an existing deployment

A deployment created by the previous bootstrap (`create_all` + ad-hoc
`ALTER TABLE`) has the baseline tables but no recorded revision. The runner
detects that shape, verifies the tables are present, and **stamps** the baseline
revision before applying the later revisions additively. No row is rewritten or
deleted. A partially created database is *not* stamped; Alembic creates what is
missing instead.

Startup is idempotent and safe to repeat.

### 3.4 Recovery and rollback boundaries

- **Schema** may move backwards with `alembic downgrade`. Revisions are written
  in pairs, so `upgrade`/`downgrade` round-trips cleanly.
- **Application rows** are never destroyed by a migration. Downgrading past
  `0002_vs5_identity` drops the VS5/VS6 tables and the added columns; it does not
  delete conversations, messages, documents, sources, traces, reports,
  definitions or run history.
- A database recorded at an unknown revision **fails closed**: the upgrade
  aborts and the data is left untouched. Recover by pointing the deployment at
  a build that knows that revision.
- Before adopting a production database, take a file-level backup. The
  migration runner is safe to re-run, but a backup is the only guaranteed
  rollback for the adoption step itself.

### 3.5 Backends

| Backend | Status |
| --- | --- |
| SQLite | Supported; upgrade, adoption, guards and idempotency proven |
| PostgreSQL | Supported and **proven** by tests that start a real PostgreSQL server, migrate it, and drive the identity layer against it |

The bundled `pgserver` package provides the PostgreSQL binaries for the test
run, so the PostgreSQL suite executes in CI rather than skipping.

---

## 4. Document lifecycle (#6)

States: `pending` → `indexing` → `ready` | `failed`; `deleting` → `deleted`.

A document is retrievable **only** when `lifecycle_state='ready'`, `indexed` is
true and `deleted_at` is null. That single predicate
(`document_lifecycle.retrievable_filter()`) is applied by every retrieval,
citation and report-pinning path, so a partially ingested, failed or deleted
document cannot surface as evidence.

Coordination is cross-process: `document_leases` is a compare-and-swap lease
(conditional UPDATE, falling back to first INSERT) that is atomic on SQLite and
PostgreSQL alike. Ingestion publishes through a compare-and-swap on
`ingest_token`; deletion clears the token first, so a concurrent ingestion
always loses deterministically and cleans up its vectors instead of publishing.
An expired lease is reclaimable, so a crashed process cannot deadlock a
document. A database trigger refuses any transition out of `deleted`.

---

## 5. Governed action runtime (VS6)

### 5.1 The registry is the capability boundary

A tool exists only if it is declared in `app/services/actions/builtin.py` with:
identifier, description, operation class (read/write), risk level, required
permissions, tenant scope, approval requirement, timeout, idempotency and
reversibility, plus a strict argument schema. Unknown arguments are rejected
rather than forwarded.

No declared tool grants shell, network, filesystem or arbitrary SQL access. The
runtime cannot execute what the registry does not declare.

### 5.2 Bounds

| Bound | Enforcement |
| --- | --- |
| Steps | `action_max_steps` at planning time |
| Time | Per-tool `timeout_seconds`; overall `action_budget_seconds` checked before each step |
| Permission | Checked at planning time **and** again immediately before each step |
| Plan validity | The plan records a permissions fingerprint; if the principal's effective permissions changed, execution is refused |
| Tenant | The plan's tenant is verified against the executing principal; a tenant switch during execution is refused |
| Approval | A write tool needs an unconsumed approval bound to the exact action fingerprint |
| Replay | Unique `(tenant_id, idempotency_key)`; a retry reuses the recorded outcome |
| Dry run | Validates and records, changes nothing |

### 5.3 Approval semantics

An approval is bound to `sha256(tenant | tool | sha256(arguments))`. Changing any
parameter changes the fingerprint, so a stale approval can never authorize a
materially different action. An approver must themselves hold the permissions
the action requires, so approval cannot launder a capability. Approvals are
consumed atomically and are single-use.

### 5.4 Evidence or abstain

A step that cannot safely run is recorded as `refused` with a reason. The
runtime never reports a success it did not observe, and every execution is
attributable to tenant, principal, role, plan, approval, tool, arguments,
result, timestamps and status.

---

## 6. API surface added in this increment

| Method | Path | Permission |
| --- | --- | --- |
| GET | `/api/auth/config` | public (no secrets) |
| GET | `/api/auth/me` | authenticated |
| POST | `/api/auth/token/exchange` | public (validated token required) |
| POST | `/api/auth/oidc/callback` | public (validated code required) |
| POST | `/api/auth/logout` | authenticated |
| GET | `/api/actions/tools` | `actions:read` |
| POST | `/api/actions/plans` | `actions:plan` |
| GET | `/api/actions/plans/{plan_id}` | `actions:read` |
| POST | `/api/actions/plans/{plan_id}/steps/{i}/approve` | `actions:approve` |
| POST | `/api/actions/plans/{plan_id}/steps/{i}/reject` | `actions:approve` |
| POST | `/api/actions/plans/{plan_id}/execute` | `actions:execute` |
| GET | `/api/actions/executions` | `actions:read` |

All pre-existing VS1–VS4 routes keep their paths and payloads; they now require
a credential and are tenant- and owner-scoped.

## 7. Browser authentication

The SPA completes the same OpenJM-native flow the API exposes, and holds only
the OpenJM session credential the backend mints.

### 7.1 Flow

| Step | Call | Notes |
| --- | --- | --- |
| Discover | `GET /api/auth/config` | public; reports `auth_mode`, `oidc_configured`, `authorization_endpoint`, `issuer`, `client_id`. No secret is returned. |
| Authorize | provider authorization endpoint | the SPA sends `response_type=code` with PKCE `S256`, `state`, and its own `redirect_uri`. |
| Callback | `POST /api/auth/oidc/callback` | the SPA verifies `state`, then exchanges `code` + `code_verifier`. The server returns an OpenJM session. |
| Use | any protected route | `Authorization: Bearer <openjm session>`. |
| Verify | `GET /api/auth/me` | server-resolved principal; also how a revoked session is detected on load. |
| Log out | `POST /api/auth/logout` | revokes server side; the SPA clears local state regardless of the response. |

A provider token can also be used directly as a bearer credential, or exchanged
through `POST /api/auth/token/exchange`. Both were exercised in live acceptance.

### 7.2 Credential handling

The session lives in `sessionStorage`, not `localStorage`. The backend issues a
bearer token rather than an httpOnly cookie, so the credential has to be
readable by this origin; per-tab storage keeps it out of long-lived persistence.
It is removed on logout, on any 401, and on a local expiry check that fires
slightly before the server's own expiry to avoid a race.

### 7.3 Authentication outcomes

* `401` means the credential is gone (expired or revoked). Local state is
  cleared and the application returns to the sign-in screen.
* `403` is an authorization outcome, not an authentication one. The session is
  retained, because a valid principal that lacks a permission is still
  authenticated.
* `state` mismatch on the callback aborts the exchange without contacting the
  token endpoint.

### 7.4 Fail closed

The deployment's mode always comes from the server. A development identity is
used only when the server reports development mode. An unreachable or
unparseable `/api/auth/config`, or an OIDC deployment with incomplete
configuration, produces a refused state rather than a workspace with no
identity. The SPA never derives a tenant, role or permission claim from its own
storage, and sends no tenant or role header: every protected decision is re-made
by the server from its membership database.
