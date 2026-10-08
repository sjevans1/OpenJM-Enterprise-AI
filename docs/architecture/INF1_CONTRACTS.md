# INF1-A — Version 1 contract proposal

Status: design specification; no API, database table or adapter below is shipped
by this documentation package. Read [the architecture](INF1_RAHKIA_SERVING_PLANE.md)
and [the implementation gate](../plan/INF1.md) first.

## Registry identities and ownership

IDs are opaque server-issued values, not names, URLs or client-provided trust
claims. All revisions are immutable snapshots with an audited active-revision
pointer. Every mutable pointer uses optimistic concurrency (`expected_revision`)
and records actor, timestamp and bounded reason. Disable/revoke blocks the next
dispatch; historical references remain readable to authorized auditors.

| Entity | Required fields | Validation / ownership |
| --- | --- | --- |
| Model release | `model_release_id`, `rahkia_alias`, `release_version`, artifact/lineage reference, tokenizer reference and revision, chat-template revision, capability set, context/output ceilings, commercial-use review reference, evaluation reference, release status | Operator-created; unique alias/version; alias maps to a pinned release via a binding; no approval inferred from the alias name |
| Runtime profile | `runtime_profile_id`, adapter kind/version, engine kind/version or opaque provider revision, immutable image/build reference where available, supported capabilities, usage method, cancellation support, cache policy, qualification evidence | No unpinned `latest` qualification; unknown capability means unsupported; self-reported health does not qualify security |
| Installation/site | `installation_id`, `site_id`, approved residency domain, operator/customer ownership, connectivity mode (`connected` / `air_gapped`), telemetry policy revision | Application installation and inference site are distinct; site is an approved administrative fact, not geolocation guessed from an IP |
| Deployment revision | `deployment_id`, revision, `model_release_id`, `runtime_profile_id`, site, inference mode, owner tenant or shared flag, endpoint reference, credential reference, lifecycle state, context/output/request-size ceilings, security qualification reference | References contain no plaintext secret; private endpoint details are operator-only; lifecycle `disabled` / `ready` / `draining` / `quarantined`; ready still requires policy/health checks |
| Tenant inference binding | `binding_id`, revision, `tenant_id`, Rahkia alias, allowed model releases/deployment IDs, allowed sites/modes/data classes, required capabilities, priority order, fallback policy, status | No wildcard tenant/deployment; local-only binding cannot select hosted; missing binding denies; client role does not imply binding authority |
| Health observation | Deployment revision, `observed_at`, expiry, model-presence result, bounded status/category | Transient observation, never authority; expired/unknown health denies new production dispatch; A uses bounded probes without a scheduler dependency |
| Routing decision | Decision/attempt IDs, tenant, binding/policy revision, deployment and model/runtime revisions or null on denial, reason, timestamp | Content-free immutable record; no endpoint, credentials, prompt hash or rejected tenants' resource identifiers |
| Usage attribution | Unique `usage_event_id`, tenant, attempt/logical-call/business-request IDs, routing decision ID, deployment/model/runtime/installation/site revisions, inference mode | One-to-one sidecar; tenant equality with M1 and routing decision enforced; no guessed legacy attribution |

Deployment and tenant-binding relations must use database constraints where
possible and transactional service validation for cross-entity rules. A local
or hosted-dedicated deployment has a non-null owner tenant and cannot be bound
to a different tenant. A hosted-shared deployment has no sole owner, but every
tenant requires its own explicit binding. Both remain subject to qualification.
Shared mode may be registered as disabled in A; production activation belongs
to B. Model revisions, underlying weights and tokenizer metadata are protected
operator data; the customer-facing name remains the Rahkia alias.

Capacity fields may be recorded in A as configuration metadata: pool ID,
accelerator type/count, tensor/pipeline parallel sizes, declared request/token
limits and capacity-reservation reference. Recording a number does not enforce
it or constitute a performance benchmark. B owns enforcement, C measurement.

## Logical request and physical attempt

| Identity | Meaning |
| --- | --- |
| `business_request_id` | One user turn, report run or workflow operation; shared by its logical model calls |
| `logical_call_id` | One purpose, e.g. structured planning or final synthesis; distinct even within the same turn |
| `attempt_id` | One actual adapter dispatch; new for an authorized retry/fallback; reused only for finalizing that same attempt |
| `parent_attempt_id` | Previous attempt when retrying/falling back; null for a primary attempt |
| `usage_event_id` | M1 finalized record for an attempt; unavailable before finalization |

All are generated/propagated by the server. Client idempotency for submitting a
business request is separate and tenant/actor scoped; a client cannot replay
another tenant's attempt ID. Do not globally deduplicate model consumption by
conversation ID, message text, token totals or a role/ordinal shared by two
logical calls. Duplicate finalization must compare tenant and immutable
attribution before returning a previous event; conflicts fail closed.

Preserve M1's `request_id` meaning for existing records. During A implementation,
reconcile actual caller conventions and either add an explicit invocation key
or use a documented versioned key derived from the server's attempt ID. Do not
rekey historical rows or silently change M2's business-request grouping. The
chosen migration and compatibility mapping are a required design-review item.

## Internal adapter interface

This is an internal service interface, not a browser request schema. Names below
are normative responsibilities; Python type/layout details may follow existing
repository conventions.

```python
class RuntimeAdapter(Protocol):
    async def probe(self, target: ResolvedDeployment) -> RuntimeHealth: ...
    async def complete(
        self, request: InferenceRequest, target: ResolvedDeployment
    ) -> InferenceResult: ...
    async def cancel(self, attempt_id: str, target: ResolvedDeployment) -> CancelResult: ...
```

| Contract | Fields and rules |
| --- | --- |
| `InferenceRequest` | Contract version; trusted tenant/actor and three correlation IDs; Rahkia alias; required capability; bounded authorized messages; temperature; positive explicit maximum output; absolute deadline; server-selected cache policy reference; source-policy epoch. Messages are transient sensitive data, excluded from registry, audit and export serializers. |
| `ResolvedDeployment` | Immutable registry/binding revisions and resolved private connection material. Constructed only after authorization. Secrets exist only in the runtime call path; this object is never serialized to customers or logs. |
| `InferenceResult` | Attempt ID; normalized actual model identity if reported; text/output-validation input; finish reason; normalized usage; timing/measurement provenance; execution certainty; provider request reference if safe. Raw response body is transient, not telemetry. |
| `RuntimeHealth` | Checked deployment/model revision, health status, observation/expiry timestamps, bounded failure code. No content, address or raw error. |
| `CancelResult` | `confirmed_stopped`, `not_supported` or `unknown`; never treat an HTTP client disconnect alone as confirmed worker termination. |

A implements buffered `complete` and `probe` for an OpenAI-compatible adapter,
with explicit profiles for the current local endpoint and private-hosted
Rahkia. vLLM/SGLang-specific extensions stay inside registered adapter profiles;
passing protocol fixtures is not engine/hardware qualification. `cancel` may
return `not_supported`; B must enforce the resulting capacity/recovery policy.
Embeddings, vision, streaming, tool calling and structured output are registered
capabilities, not silently promised by text completion. A introduces no new
generation modality or public endpoint.

Adapters must not retry, choose a different tenant/model/site, infer authority
from a URL, fetch remote model code or reinterpret a missing entitlement as
approval. Restrict runtime extras to reviewed per-profile fields. Reject
attempts to override `model`, messages, output/deadline bounds, cache identity,
destination or security headers. Validate the actual returned model against the
registered mapping; a mismatch fails safely and still records observed usage.

## Routing algorithm and audit record

Use deterministic priority order in A. Do not introduce least-cost selection,
an ML router, automatic alias upgrades or autoscaling.

1. Require an active server-resolved tenant/actor and approved binding.
2. Resolve the alias to an allowed immutable model release.
3. Intersect allowed deployments with owner/shared bindings, site/residency,
   data-class requirements, capabilities and qualified security profile.
4. Apply lifecycle and non-stale health checks; use configured priority with a
   stable deployment-ID tie-breaker. Draining/quarantined/disabled are ineligible.
5. Check byte/message/context/output limits against the strictest of request,
   binding, deployment and model constraints. Use the registered tokenizer/chat
   template where available; an uncertain estimate cannot prove a hard context
   bound. Apply a qualified conservative bound or deny before dispatch.
6. Record the selected immutable revisions and bounded decision reason. A route
   result is not a reusable authorization token: re-evaluate immediately before
   each dispatch and after queueing in B.

Reason vocabulary: `allowed`, `tenant_inactive`, `binding_missing`,
`binding_revoked`, `model_not_allowed`, `site_not_allowed`,
`capability_unsupported`, `isolation_unqualified`, `deployment_unavailable`,
`health_stale`, `request_too_large`, `context_limit`, `policy_changed`.
Denied decisions have no selected deployment. Customer responses use safe
categories without exposing other deployments or registry inventory.

## Token and timing normalization

| Field | Semantics |
| --- | --- |
| `input_tokens`, `output_tokens`, `total_tokens` | Non-negative strict integers, not booleans; total equals input + output under the adapter's qualified mapping |
| `cached_input_tokens`, `reasoning_tokens` | Nullable subsets of input/output respectively; never added again to total; incompatible provider semantics require an explicit mapping |
| `usage_source` | Existing M1 enum: `provider_reported` or `estimated` |
| `count_method` | `runtime_reported`, `tokenizer_estimate`, `character_estimate` or `unknown`; tokenizer/model/template revisions required for tokenizer estimates |
| `execution_certainty` | `not_dispatched`, `completed`, `partial`, `unknown`; transport timeout is not proof of no consumption |
| `usage_completeness` | `complete`, `partial`, `unknown`; prevents numeric placeholders from being presented as confirmed consumption |
| `gateway_queue_ms` | Monotonic time in Rahkia admission queue; A has no queue, so measured zero only when known |
| `runtime_queue_ms` | Qualified runtime-provided queue time or null; separate from gateway queue |
| `service_latency_ms` | Gateway dispatch to terminal response/cancel outcome, including transport and runtime waiting |
| `ttft_ms` | Gateway dispatch to first generated content token observed by streaming, or explicitly tagged runtime measurement; null for ordinary buffered completion |
| `generation_ms`, `output_tokens_per_second` | Qualified decode interval and corresponding throughput; null when not observable or denominator is invalid |
| `gpu_seconds` | Nullable infrastructure observation with method `measured` / `allocated` / `estimated` / `unknown`; never inferred as exact from HTTP latency |

Every non-token measurement carries origin (`gateway` / `runtime` / `collector`),
scope (`attempt` / `pool_window`) and quality. Durations are finite and
non-negative; timestamps use UTC for correlation and monotonic clocks for elapsed
time. Model text is not a trusted source of usage or timing values. Malformed
usage is rejected and marked unknown/estimated by explicit policy, never accepted
as zero provider usage. A safe failure category is persisted separately from
provider exception text.

M1 cannot currently express every uncertainty state. Preserve its accepted
schema semantics, carry completeness in the sidecar, and make M2/M3 visibly
separate uncertain consumption. Missing TTFT/GPU measurements stay null, not
zero. Do not rewrite accepted historical estimates.

## Telemetry export contract

Default `off`; the optional `aggregate_only_v1` payload has **only** these fields:

| Field | Meaning |
| --- | --- |
| `schema_version`, `export_id`, `installation_id`, `sequence` | Versioned envelope and deduplication identity |
| `commercial_tenant_ref`, `window_start`, `window_end`, `generated_at` | Approved non-personal account mapping and UTC reporting window |
| `telemetry_policy_revision`, `signing_key_id` | Local export authorization and signature verification context |
| `rows` | Bounded rows grouped by Rahkia alias, inference mode, approved deployment export reference and usage/count quality |
| Row counters | Attempt/success/failure/retry/fallback counts; input/output/total and nullable cached/reasoning token sums; unknown-usage count |
| Optional row summaries | Coarse queue/latency histogram buckets and GPU-time totals with method/coverage; absent when unavailable |
| `correction_of` | Null for the initial window or prior export ID for an append-only correction |
| `payload_digest`, `signature` | Digest/signature over a specified canonical encoding excluding these envelope fields; algorithm/key rotation pinned in C |

Unknown fields at every nesting level are rejected. Signed configuration fixes
which dimensions, window size and detail level are allowed; tenant/site policy
may reduce them. Aggregation-only means no per-call rows or content-derived
identifiers. Low-volume summaries can still identify activity; access controls
and agreed minimum reporting granularity remain necessary.

A defines/tests a pure serializer with synthetic fixtures; it sends nothing.
C owns the exporter, spool/import protocol, signing implementation, acknowledgements,
replay/conflict checks, corrections and operational acceptance. Algorithm choice
must reuse the project's approved security primitives when that implementation
is scoped. There is no new mandatory cloud account or callback in A.

## Proposed API surface and migration compatibility

After BV3 integration, A may add bounded `/api/platform/inference` registry
operations for model releases, runtime profiles, deployments and tenant bindings,
plus redacted routing-decision reads. Mutation authorization is
`platform:inference:admin`; reads use `platform:metadata:read`. Paginate lists;
require tenant filters for tenant-level decision history; never return secrets.
Private endpoint/credential provisioning stays in trusted operator configuration,
not general API responses. No new client-admin form or end-user runtime selector.

Compatibility bootstrap reads the existing operator-owned endpoint settings into
one explicit deployment/binding only after the installation tenant/site is
specified. It does not bind all present/future tenants. Retain the accepted
development-only default tenant where already valid; production cannot invent a
tenant/site. Legacy environment variables stay supported during the transition,
but an explicit registry policy cannot be bypassed through the legacy gateway.

Create attribution alongside M1 finalization in the same transaction, retaining
the existing SAVEPOINT and pending-failure behavior. Enforce the usage-event
tenant match and one-to-one relation. An attribution failure must surface as a
recording failure, not quietly return unattributed success in the INF1 path.
A still does not prove crash-safe hard-credit accounting: B/M3 durable intent
and settlement are mandatory before commercial hard-cap claims.

Rollback must disable new routes and preserve ledgers, registry revisions and
sidecars; reverting executable code is preferred to dropping evidence tables.
In a multi-tenant production installation, rollback must not revive a legacy
global endpoint path that evades explicit tenant bindings. Test upgrade and
rollback compatibility on SQLite and PostgreSQL using synthetic pre-INF1 data.
