# M3 / INF1-B shared contract

Status: agreed before either stream edited shared schema. This document is
authoritative for both lanes and lives on the M3 branch because M3 is the first
stream in the agreed sequence; INF1-B references it.

## 1. Ownership split

| Concern | Owner | Never inferred by the other lane |
| --- | --- | --- |
| Commercial eligibility | M3 | INF1-B must never read a tenant role, a plan name or an allowance to decide capacity |
| Allowance and credit balance | M3 | INF1-B must never compute or hold a credit value |
| Reservation value and its lifetime | M3 | INF1-B must never invent or extend a reservation |
| Billing period | M3 | INF1-B has no notion of a period |
| Commercial settlement | M3 | INF1-B never settles usage |
| Capacity, admission, queue and concurrency | INF1-B | M3 must never infer runtime capacity from a credit balance, a plan or a reservation |
| Runtime and deployment availability | INF1-B | M3 never decides placement |
| Isolation and overload behaviour | INF1-B | M3 never decides isolation |
| Usage metering | M1 (accepted) | neither lane rewrites a metered row |
| Aggregation and visibility | M2 (accepted) | neither lane recomputes aggregates |

The two lanes meet at exactly one value: an opaque reservation handle issued by
M3 and presented to INF1-B. Neither lane parses it for meaning.

## 2. Required sequence

```
Authenticate
  -> Authorize                     (accepted identity and tenant resolution)
  -> M3 commercial reservation     (eligibility, allowance, hard cap, hold)
  -> INF1-B capacity admission     (tenant and pool concurrency, queue)
  -> wait / revalidate if queued   (tenant, source, binding, reservation)
  -> test-model execution          (existing approved path)
  -> M1 usage record               (accepted metering)
  -> M3 finalize / reconcile       (settle once, or release on failure)
  -> release capacity lease
```

Rules that follow, and that both lanes must implement:

1. **Capacity rejection before dispatch releases the commercial reservation
   exactly once.** A rejection is not a settlement.
2. **A queued request revalidates** tenant state, source authorization, binding
   and reservation state before dispatch. Waiting is not consent.
3. **A crash at reserve, enqueue, dispatch, response or finalization must not
   duplicate usage or credit settlement.** Every step is idempotent on a server
   issued key.
4. **Uncertain consumption stays explicit.** A transport timeout is not proof of
   zero consumption and must not settle as zero.
5. **Pre-dispatch and post-dispatch authority are separate, and M3 owns the
   boundary.** Before dispatch the governing fact is tenant state: an inactive
   tenant may not create a reservation and may not dispatch a queued one. After
   dispatch the governing fact is the recorded execution state, not the tenant's
   current status. Once dispatch was recorded while the tenant was active, a later
   suspension must not prevent settlement, because that would make produced
   inference free. M3 records dispatch through a trusted seam that the execution
   path calls while the pre-dispatch conditions still hold; no caller claim can
   substitute for it, and an undispatched reservation cannot settle. Release is a
   pre-dispatch operation, and expiry applies only to undispatched holds.

   **The execution lifecycle is explicit, and the two lanes record the same
   boundary:**

   ```
   undispatched --begin_dispatch--> dispatching --mark_execution_dispatched--> dispatched
                                                  \--mark_execution_uncertain--> uncertain
   ```

   * `begin_dispatch` is M3's counterpart of INF1-B's `mark_dispatched`. Both lanes
     record the model-network dispatch boundary, so a crash after it means
     consumption is unknown on both sides rather than zero.
   * `dispatching` is neither releasable nor expirable. A stale `dispatching`
     record is recovered as `uncertain`, never returned to `undispatched`.
   * `dispatched` means the outcome is known, so actual usage settles normally and
     a successful request is never charged the full reservation when the real usage
     was lower.
   * `uncertain` means the outcome is unknown and settles conservatively at the
     reserved worst case, never zero.
   * Transitions are idempotent on replay and cannot skip the boundary or move
     backwards.
5. **Billing exhaustion blocks new billable execution** but must not block
   reading existing history, tenant administration or recovery operations.

## 3. Migration order and reconciliation rule

Both lanes need new tables, so the chain order is fixed and the heads are
allocated now:

| Order | Stream | Revision id | `down_revision` at freeze |
| --- | --- | --- | --- |
| 1 | M3 | `0016_m3_entitlements` | `0015_inf1_inference_registry` |
| 2 | INF1-B | `0017_inf1b_admission` | `0015_inf1_inference_registry` |

Each branch is independently valid on its own: it carries exactly one head and
its migration upgrades cleanly from `0015`. They cannot both be true on `main`
simultaneously, so the agreed reconciliation is:

> When the first of the two merges, the second replays onto the new `main` and
> changes its `down_revision` to the first stream's revision id. That is a
> one-line change plus a migration test rerun, the same replay already performed
> for the BV3 stack and for M2 onto INF1-A.

This is the only shared schema work, and it is serialized by this rule rather
than by blocking either lane from starting.

Both lanes add tables only. Neither alters an existing table, column or
constraint, and neither rewrites a historical row. The accepted M1 ledger and the
M2 read model are not modified.

## 4. Shared model seam

Both lanes must add their ORM models to `backend/app/models.py`, which is the
single point of textual overlap between the branches. This is deliberate: the
repository keeps one model module, and splitting it would be a larger change than
the conflict it avoids.

Convention for both lanes:

- table names are plural and prefixed by their lane (`entitlement_*`,
  `credit_*`, `admission_*`, `capacity_*`);
- every tenant-owned table uses `tenant_column()`;
- immutability is expressed with a `CheckConstraint` or a unique key, not only in
  service code;
- money and credit amounts are integer minor units (see 5), never `Float`.

## 5. Fixed-precision accounting rule

Both lanes, and any future lane, must hold monetary and credit values as integer
minor units with an explicit scale, in `BigInteger` or `Integer` columns and
Python `int`. No `float` may participate in a balance, a reservation value, a
price, a conversion or a threshold comparison. A ratio or a percentage is
computed as an integer comparison against the allowance, not as a floating point
fraction. This is an acceptance criterion, not a style preference.

## 6. Service seams

| Seam | Module | Owner |
| --- | --- | --- |
| Plans, subscriptions, allowances, credits, reservations, periods | `app/services/entitlements/` | M3 |
| Capacity, admission, queue, leases | `app/services/admission/` | INF1-B |
| Client administration read surface | `app/api/admin.py` and `app/services/client_admin.py` | M3 extends; INF1-B does not touch |
| Platform plane | `app/api/platform.py` and `app/api/platform_usage.py` | M3 may add a bounded entitlement metadata route in its own module; INF1-B does not touch the platform plane |
| Admission surface | new module under `app/api/` | INF1-B |
| Router registration | `app/main.py` | both add one line; the only other textual overlap |

Neither lane edits the other's module, service or API surface.

## 7. What neither lane implements

Payments, invoices, tax, accounting integration, external payment processors,
autoscaling, Kubernetes or operator machinery, GPU scheduler integration, Rahkia
production runtime cutover, live vLLM/SGLang qualification.

Rahkia production integration remains deferred to the specialist OpenJM team.
Both lanes are accepted against the current approved test-model path.
