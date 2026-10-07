# Performance, load and sizing (VS8 Workstream J)

OpenJM publishes a **measured, bounded baseline**, not marketing claims. Run the
harness on the deployment hardware to reproduce numbers for that hardware.

## Harness

```bash
# liveness throughput/latency under concurrency
python scripts/load_baseline.py --base-url http://127.0.0.1:8000 \
  --users 20 --seconds 20 --mode health

# readiness (checks dependencies) under concurrency
python scripts/load_baseline.py --base-url http://127.0.0.1:8000 \
  --users 10 --seconds 20 --mode ready

# chat path (requires a reachable model endpoint)
python scripts/load_baseline.py --base-url http://127.0.0.1:8000 \
  --users 4 --seconds 20 --mode chat --out docs/load-chat.json

# tenant isolation is exercised automatically: with --users >= 2, workers are
# split across two X-OpenJM-Tenant values so responses must remain separated.
```

Recorded per run: concurrency, duration, throughput, error rate, latency
distribution (p50/p90/p99/mean/max), status histogram, and hardware (platform,
CPU count, Python version).

## Representative workloads

- concurrent authenticated API requests (`/api/health`, `/api/ready`);
- chat / conversation requests (Chat + Knowledge + Data + Hybrid);
- Knowledge retrieval (bounded by `rag_top_k + rag_neighbor_max_chunks`);
- structured queries (bounded by `structured_max_rows` and the SQL timeout);
- report listing / rerun where enabled (bounded by the run budget);
- connector sync and scheduler tick (both bounded per tick);
- document ingestion (bounded by the ingest lease).

## Measured baseline (this candidate, recorded evidence)

The candidate baseline is recorded in `docs/VS8_ACCEPTANCE_REPORT.md` for the
host it was measured on. Because the model path dominates chat latency, the
health/readiness baseline is the stable, comparable figure; chat numbers are
reported only with the model identifier and hardware alongside them.

## Sizing guidance

Sizing is derived from the measured evidence, not guessed:

- **Small** (pilot / single team): 2 vCPU, 4 GB RAM, shared PostgreSQL,
  20 GB persistent storage. Comfortable for the measured health/readiness
  baseline and light chat concurrency.
- **Medium** (department): 4 vCPU, 8 GB RAM, dedicated PostgreSQL, 100 GB
  storage, with the model served on a separate private host.

Adjust to the recorded baseline for your hardware and model. Performance
failures must never weaken authorization or bypass evidence checks: the
authorization and evidence layers are not tunable, and load is bounded by
existing per-request budgets.

## Resource limits

Deployment limits are explicit: systemd `MemoryMax`/`CPUQuota`/`TasksMax` and
Compose `deploy.resources` (or systemd) rather than unbounded growth. Database
pool saturation is observable via `/api/ready` (`database.checked_out/size`) and
`openjm_db_pool_checked_out`.
