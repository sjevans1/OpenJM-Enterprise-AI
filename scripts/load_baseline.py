#!/usr/bin/env python
"""VS8 Workstream J — representative load / tenant-isolation baseline.

Measures a bounded, documented workload against a running OpenJM backend and
prints a JSON baseline (latency distribution, error rate, throughput, memory
growth). It makes *no* marketing claims: the numbers are what this harness ran
on this hardware, recorded so sizing advice is evidence-based.

Usage:
    python scripts/load_baseline.py --base-url http://127.0.0.1:8000 \
        --users 20 --seconds 20 --mode health
    python scripts/load_baseline.py --base-url http://127.0.0.1:8000 \
        --users 8 --seconds 20 --mode chat      # needs a reachable model
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class Sample:
    latencies: list[float] = field(default_factory=list)
    errors: int = 0
    statuses: dict[str, int] = field(default_factory=dict)

    def record(self, elapsed: float, status: int) -> None:
        self.latencies.append(elapsed)
        self.statuses[str(status)] = self.statuses.get(str(status), 0) + 1
        if status >= 400:
            self.errors += 1


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return round(ordered[index] * 1000, 2)  # ms


def _rss_kb() -> int:
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


async def _worker(base_url: str, mode: str, deadline: float, sample: Sample, tenant: str) -> None:
    headers = {"X-OpenJM-Tenant": tenant} if tenant else {}
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        while time.monotonic() < deadline:
            started = time.perf_counter()
            try:
                if mode == "chat":
                    response = await client.post(
                        "/api/chat",
                        json={"message": "Summarise the last quarter briefly.", "mode": "chat"},
                        headers=headers,
                    )
                elif mode == "ready":
                    response = await client.get("/api/ready", headers=headers)
                else:
                    response = await client.get("/api/health", headers=headers)
                sample.record(time.perf_counter() - started, response.status_code)
            except httpx.HTTPError:
                sample.record(time.perf_counter() - started, 599)


async def run(args) -> dict:
    deadline = time.monotonic() + args.seconds
    sample = Sample()
    tenants = [""] if args.users < 2 else ["tnt-isolation-a", "tnt-isolation-b"]

    rss_start = _rss_kb()
    started = time.perf_counter()
    await asyncio.gather(
        *(
            _worker(args.base_url, args.mode, deadline, sample, tenants[i % len(tenants)])
            for i in range(args.users)
        )
    )
    wall = time.perf_counter() - started
    rss_end = _rss_kb()

    total = len(sample.latencies)
    return {
        "harness": "openjm-load-baseline-vs8",
        "mode": args.mode,
        "concurrency": args.users,
        "duration_seconds": round(wall, 2),
        "hardware": {
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
        },
        "requests": total,
        "throughput_rps": round(total / wall, 2) if wall else 0.0,
        "error_rate": round(sample.errors / total, 4) if total else 0.0,
        "latency_ms": {
            "p50": _percentile(sample.latencies, 50),
            "p90": _percentile(sample.latencies, 90),
            "p99": _percentile(sample.latencies, 99),
            "mean": round(statistics.fmean(sample.latencies) * 1000, 2) if total else 0.0,
            "max": round(max(sample.latencies) * 1000, 2) if total else 0.0,
        },
        "statuses": sample.statuses,
        "harness_rss_delta_kb": rss_end - rss_start,
        "notes": "Harness process RSS delta only; server-side memory must be read "
                 "from the backend process or its metrics endpoint.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenJM load / tenant-isolation baseline")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--mode", choices=["health", "ready", "chat"], default="health")
    parser.add_argument("--out", default=None, help="write the baseline JSON here")
    args = parser.parse_args()

    result = asyncio.run(run(args))
    print(json.dumps(result, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    return 0 if result["error_rate"] < 0.5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
