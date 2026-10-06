# Product Boundary: Enterprise AI and Workspace

Status: boundary record for issue #9, updated alongside VS5/VS6.
The optional Workspace connector is **not implemented**. Nothing in this
document claims otherwise. It records the separation that is already enforced in
code and tests, and the acceptance cases the connector must satisfy before it
can be merged.

## Scope

OpenJM Workspace (`sjevans1/Workspace-Platform`) and OpenJM Enterprise AI
(`sjevans1/OpenJM-Enterprise-AI`) are two independently deployable products.
Neither requires the other to start, to serve its core users, or to pass its
tests. Integration is optional, opt in, and one way: an Enterprise AI connector
calls Workspace's documented, versioned APIs. Workspace never calls into
Enterprise AI.

## Non-negotiable separation

Independent across every one of these axes:

- GitHub repository, packaging, release cadence, deployment.
- Ports and configuration.
- Database, object storage, vector store.
- Users, tenants, memberships, roles.
- Secrets, credentials, tokens, session state.
- Runtime availability.

Explicitly forbidden from Enterprise AI toward Workspace:

- No source imports.
- No shared ORM models or tables.
- No cross repository migrations or monorepo linking.
- No shared secret values.
- No direct reads of Workspace's PostgreSQL, object store, or vector store.
- No runtime dependency on Workspace being reachable.

Explicitly forbidden from Workspace toward Enterprise AI: no model, runtime,
retrieval, embedding, or agent orchestration dependency in Workspace core.

## What each product owns

Workspace owns its pages, editor, databases, identity, ACL/row-level security,
object storage, audit trail, change events, and published integration APIs.

Enterprise AI owns retrieval and indexes, embeddings, LLM and provider routing,
SQL, RAG and hybrid planning, evidence provenance, chat experience, and the
connector itself. Integration credentials live in the Enterprise AI vault, never
in source files.

## Identity boundary

This is the VS5 consequence of the separation. Enterprise AI has its own
identity system:

- Its own OIDC issuer, audience, and JWKS.
- Its own `Principal` derived server side from a validated token.
- Its own tenants, memberships, roles, and permissions.
- Its own session and audit records.

Enterprise AI does not consume Workspace sessions, cookies, tokens, or secrets,
and does not read Workspace's identity store. The Workspace Keycloak/OIDC
implementation was used as a design reference only. A Workspace user identity
has no meaning in Enterprise AI until an administrator maps it explicitly. When
the connector arrives, that mapping is an explicit, revocable record, never an
inference.

## Enforcement today

The separation is enforced automatically rather than by convention.

- `backend/tests/test_product_independence.py` fails the build if Enterprise AI
  imports Workspace code, shares tables, or hard codes a Workspace secret or
  endpoint. This runs in CI on every change.
- VS5 replaced the development trust shortcuts. Every protected operation
  derives its principal and tenant server side and revalidates authorization at
  the point of use, so a Workspace style "trusted header" shortcut cannot
  reintroduce a single user assumption.
- The tenant isolation suite proves that one tenant cannot reach another
  tenant's documents, vector evidence, conversations, structured sources,
  reports, history, audit records, or execution traces.

## Connector acceptance cases (required before any connector merge)

Standalone, with the connector disabled, which is the default:

- Workspace boots, creates and edits pages, collaborates, and backs up and
  restores with Enterprise AI absent.
- Enterprise AI boots, chats, uses local uploads with RAG, and runs structured
  SQL with Workspace absent or disabled.

With the connector enabled by explicit configuration:

- Create a document in Workspace, ingest it through the API, and cite it in an
  answer.
- Edit the document and observe the updated answer.
- Revoke the source ACL or the membership and observe Enterprise AI refuse the
  evidence on the next request.
- Delete the document or disconnect and observe stale chunks removed or
  quarantined.
- Two tenants and two users demonstrate no cross tenant or cross principal
  evidence leakage.

## Non-negotiable connector rules

1. An administrator configures a Workspace base URL and a tenant scoped service
   credential, and can test, revoke, and disable it safely. Cookies and passwords
   are never shared.
2. The initial pull enumerates only granted resources through versioned APIs and
   tags ingested content with `connector=workspace` plus tenant, source,
   resource, and revision identifiers, so namespacing cannot collide with
   uploaded documents or SQL data.
3. Incremental updates use signed webhook verification with dedupe, bounded
   overlapping events cursor polling, and conservative stale evidence cleanup.
4. A readable service token never authorizes evidence by itself. The connector
   checks the current end user's permission through the documented permissions
   API after resolving an explicit user mapping. Ambiguous mapping, revoked
   membership, timeout, and API failure all deny evidence and quarantine stale
   cached chunks.
5. Disconnect, credential rotation, catch up after downtime, bounded retries,
   idempotency, purge on revocation, and connector health metrics must all work
   without leaking tenant content.

## Sequencing

No connector code until Workspace PR #57 is accepted or safely closed and the
blocked Enterprise AI hybrid PR #8 is reconciled. CI and PR acceptance stay per
repository. Issue #9 remains an architecture and acceptance record until then.
