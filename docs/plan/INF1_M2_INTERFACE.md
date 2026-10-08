# INF1-A / M2 interface agreement

Status: agreed before either lane touched shared schema. INF1-A owns this file
because INF1-A is the only lane that changes schema.

The accepted plan already fixes the shape of this agreement in
[the INF1 delivery plan](INF1.md) under "M2 interface agreement". This document
records how those rules are honoured in code, so a reviewer can check the two
lanes against one contract instead of against each other.

## 1. Migration order

| Lane | Migration | Schema it owns |
| --- | --- | --- |
| INF1-A | `0015_inf1_inference_registry` | the Rahkia registry (model releases, runtime profiles, deployments, tenant bindings, health observations, routing decisions) and the one-to-one usage attribution sidecar |
| M2 | none | M2 adds **no** table, column or constraint |

Conclusions that follow:

- There is no migration race between the lanes. INF1-A allocates the next id in
  the accepted chain (`0014` -> `0015`); M2 allocates nothing.
- M2 is a read model. It aggregates the immutable M1 ledger
  (`model_usage_events`) and left joins the attribution sidecar when it exists.
- Neither lane edits the other's migration, and neither rewrites accepted M1
  rows. Historical usage stays exactly as recorded.

## 2. The one-to-one usage attribution contract

`inference_usage_attributions.usage_event_id` is a unique foreign key to
`model_usage_events.id`. One M1 event has at most one sidecar, so a replayed
finalization cannot double count and two competing attributions cannot coexist.

- The sidecar is written **inside** the same transaction as M1 finalization,
  retaining M1's SAVEPOINT and pending-failure behaviour.
- The sidecar tenant must equal the M1 event tenant and the routing decision
  tenant. Any mismatch fails closed with an attribution error, which surfaces as
  a recording failure rather than an unattributed success.
- A replay of the same `attempt_id` returns the existing row. A replay of a
  different attempt against the same event is a conflict and is refused.
- An M1 row with no sidecar is not an error: it is historical usage and M2
  reports it as explicitly `legacy_unknown`.

Identity meanings (accepted contract):

| Identity | Meaning |
| --- | --- |
| `business_request_id` | one user turn, report run or workflow operation |
| `logical_call_id` | one purpose inside that request, distinct even in one turn |
| `attempt_id` | one actual adapter dispatch, new for an authorized retry or fallback |
| `parent_attempt_id` | the attempt being retried or fallen back from, null for a primary |

## 3. No fabricated deployment identity

Neither lane may derive a deployment, site or inference mode from a provider URL,
a model name or any other incidental string. INF1-A records that identity only
from an explicit registry row; until INF1-A has landed, M2 groups every row as
`legacy_unknown` and says so, rather than guessing a plausible value.

## 4. Aggregation rules M2 must hold

- A left join, never an inner join: unattributed usage is never dropped.
- No one-to-many fan-out: a row is counted once, so counts and token sums cannot
  be multiplied by a second matching telemetry row.
- Provider-reported, estimated and uncertain coverage stay separate. They are
  never summed into a single figure presented as provider-grade.
- Tenant scoping is a SQL predicate, never a post-filter.
- Exporting usage to a client administrator is a separate concern from sending
  installation telemetry upstream to OpenJM. Neither lane implements the
  upstream exporter.

## 5. Shared surfaces

- INF1-A owns `app/core/platform.py`, `app/api/platform.py`, `app/models.py` and
  the migration chain. INF1-A adds the `platform:inference:admin` capability.
- M2 owns the tenant administration usage surface and the aggregation service.
  M2 adds its platform-facing usage metadata route in its own module rather than
  editing the platform plane, so the two lanes do not collide in one file.
- Neither lane edits the other lane's API module. Where both must register a
  router, that is the only line of overlap in `app/main.py`.
