# Connector authorization state isolation and retrieval completeness

Plan revision: `connector-auth-1`
Repository: **sjevans1/OpenJM-Enterprise-AI only**
Base: `main` at `aacc9f2`
Authorization: product owner, 2026-10-11, four decisions recorded below.
Required CI: `Backend / Python 3.11` and `Frontend / Node 22`.

## Objective

Correct two confirmed connector authorization defects so that:

1. a principal-specific authorization decision never mutates shared content state, and
2. authorized connector documents are not silently excluded from retrieval by an arbitrary
   candidate bound.

Package CA1 (state isolation) ships first, on its own tests and its own exact-head CI.
Package CA2 (retrieval completeness) ships separately with independent evidence.

## Why now: confirmed evidence

Both defects were reproduced by execution against `7f43374` and confirmed present identically
on `origin/main` at `aacc9f2` (provider paths and line numbers match; the connector code is
byte-identical between the two revisions).

**Defect 1, cross-user availability.** `authorize_resource` declares
`quarantine_on_deny: bool = True` (`services/connectors/authorization.py:195`). Every denial
path routes through `_deny`, which calls `quarantine_resource`, which mutates the shared
`ExternalResource` row and the shared Knowledge document
(`services/connectors/ingest.py:333`, `:341`). Nothing in that state is per-principal.

Reproduced by reversing the call order in
`test_two_users_do_not_leak_user_specific_evidence` and changing nothing else:

    allowed_c=set() expected {'6c29f29a-19c9-403f-9533-71fd237623d0'}
    1 failed in 1.01s

The existing test passes unchanged, because it authorizes the allowed principal before the
denied one. It therefore never observes the property. Three routes reach the defect with the
flag left at its default: `user_not_mapped`, `access_revoked`, and
`authorization_unavailable` (timeout, outage, rate limit). A transient provider timeout
durably removes shared content for a whole tenant.

**Defect 2, retrieval completeness.** `authorized_connector_document_ids` selects candidates
with `.limit(limit)` where `limit` defaults to `MAX_AUTHORIZATION_CHECKS = 100`
(`authorization.py:58`, `:367`), with no ordering. Reproduced with 150 resources all
authorized for one principal:

    PROBE2: bound=100 seeded=150 authorized=100 denied=0
    50 documents, all authorized, unreachable

Both defects fail closed, so neither leaks content.

## Package CA1 — authorization state isolation

### Old versus new semantics

| Condition | Old behaviour | New behaviour |
| --- | --- | --- |
| Principal unmapped for the connector | Deny that request, and quarantine the shared resource for everyone | Deny that request only. Shared resource state unchanged |
| Provider denies this principal on this resource | Deny that request, and quarantine the shared resource for everyone | Deny that request only. Shared resource state unchanged |
| Provider authorization unavailable (timeout, outage, rate limit, contradictory) | Deny, and quarantine the shared resource for everyone | Deny that request only, fail closed. Shared resource state unchanged |
| Connector disabled, credential revoked or superseded, connector not found | Deny, and quarantine (currently) | Deny that request only. Shared resource state unchanged |
| Resource content confirmed invalid, deleted at source, or integrity failure | Quarantine | Quarantine, unchanged. This remains a content-scoped lifecycle operation |
| Connector-scoped content (no per-user provider model) | Allow as tenant-wide | Allow as tenant-wide, unchanged |

The governing distinction is that the scope of a state change must match the scope of the
failure. A principal-scoped failure proves something about one reader. A content-scoped
failure proves something about the content. The old code applies a content-scoped remedy to a
principal-scoped failure.

### Required change

- Authorization checks become non-mutating by default. A principal-specific check must not
  call `quarantine_resource`.
- Prefer a structural separation over a permanently relied-upon boolean: authorization
  decides, and explicit, separately named lifecycle operations mutate content. If a flag
  remains for compatibility, it must default to the safe, non-mutating value, and the
  retrieval path must not pass a mutating value.
- Global quarantine remains available for confirmed resource-wide conditions, through an
  explicitly authorized lifecycle operation rather than as a side effect of an authorization
  decision.
- Auditability is preserved without a second permission authority: a denial is enforced for
  the current request and remains effective on subsequent requests because the provider is
  consulted again. No principal-specific allow/deny decision is persisted, and no new table or
  migration is introduced. In particular, principal-specific decisions must not be written to
  the shared `permission_state`.
- Inspect and align every caller for consistent behaviour:
  `services/orchestrator.py:223`, `services/document_policy.py:206`, `services/tools.py:289`,
  `api/reports.py:159`, and the connector sync and reconciliation paths that legitimately
  quarantine for content-scoped reasons.

### Exclusions

- No new database table or migration.
- No change to the provider authorization contract, the mapping contract, or tenant isolation.
- No weakening, deletion, or disabling of any existing security test.
- No change to the OIDC, entitlement, report-authorization or tool-authorization boundaries.
- Defect 2 is explicitly out of scope for CA1.

### Invariants that must hold after the change

1. A denied principal never receives unauthorized evidence, under any path.
2. A denied principal does not reduce any other principal's retrieval in the same tenant.
3. The result is independent of the order in which principals are evaluated.
4. A provider timeout or outage fails closed for the requesting principal without globally
   quarantining shared content.
5. Availability recovers without a reconciliation run when the content itself remains valid.
6. Confirmed content-wide quarantine and deletion still work, and still fail closed.
7. Tenant isolation, report authorization, tool authorization and Knowledge security are
   unchanged.

### Required tests

Existing tests asserting global quarantine after a principal-specific denial are revised, not
removed, and their security intent is preserved. Each revised test documents why the previous
assertion was incorrect.

New regression tests, all required:

1. A denied user never receives unauthorized evidence.
2. A denied user does not affect an authorized user's retrieval.
3. The outcome is independent of call order, asserted in both orders.
4. Provider timeout and outage fail closed without globally quarantining shared content.
5. Availability recovers without a reconciliation requirement while the content remains valid.
6. Confirmed content-wide quarantine and deletion still operate.
7. Tenant isolation, report authorization, tool authorization and Knowledge security remain
   intact.

### Acceptance and evidence

- Focused connector security suite green locally, with the revised tests and the new
  regression tests named in the PR.
- Exact-head `OpenJM CI` green with `Backend / Python 3.11` actually succeeding and
  `Frontend / Node 22` present. A skipped or missing expected job is not acceptance.
- PR records: regression evidence, old versus new semantics, affected call sites, and any
  remaining limitation.
- No merge without explicit human approval.

## Package CA2 — retrieval completeness (separate)

Not started under CA1. Its own plan revision, its own tests, its own exact-head CI.

Objective: design and implement a bounded, query-relevant retrieval and authorization process
that does not silently exclude otherwise authorized documents because they fall outside an
arbitrary first-100 selection.

Constraints:

- Do not merely raise the candidate limit.
- Fail-closed authorization is maintained.
- Budget handling is deterministic and reported, not silent.
- Failure behaviour is non-enumerating.
- Tested with at least 150 authorized resources, varied document ordering, selective
  permissions, and relevant documents positioned outside the initial candidate window.

## Out of scope for both packages

- Cloud parsing, embedding, vector store or connector sourcing decisions.
- Any change to the model gateway, chunking policy, or retrieval ranking.
- The INF1 serving plane, M3 entitlements, and Workspace integration.

## Rollback

CA1 is a behavioural change confined to the connector authorization and ingestion path. It
introduces no schema change, so rollback is a revert of the branch. The previously asserted
quarantine behaviour can be restored by reverting, which is why the revised tests must state
the intended semantics rather than only the observed behaviour.
