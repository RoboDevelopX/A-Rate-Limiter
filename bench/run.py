"""Latency and throughput of ``check()`` per algorithm and backend.

Method: for each backend and algorithm, warm up, then time every ``check()`` call with
``perf_counter_ns``. "sequential" runs one coroutine (pure per-call latency); "concurrent"
runs many coroutines sharing one event loop (and, for Redis, a 64-connection pool), which
is how an async API server uses the limiter. Keys rotate over 1,000 callers with a generous
quota, so this measures the common "allowed" path. Redis runs on the same machine, so
network latency is excluded; add your network round trip for a remote Redis.

Usage: ``python -m bench.run [--requests N] [--concurrency C] [--redis-url URL]``
"""

import argparse
import asyncio
import json
import os
import resource
import statistics
import time
from pathlib import Path
from typing import Any

from redis.asyncio import BlockingConnectionPool, Redis

from bench.environment import describe
from mlratelimit import Limiter, MemoryBackend, Quota, RedisBackend
from mlratelimit.algorithms import ALGORITHMS
from mlratelimit.backends.base import Backend

RESULTS = Path(__file__).parent / "results"
KEYS = [f"user-{i}" for i in range(1000)]
QUOTA = Quota(limit=1000, period=1)


def _process_cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


async def _redis_cpu_seconds(client: Redis | None) -> float:
    if client is None:
        return 0.0
    info = await client.info("cpu")
    return float(info["used_cpu_user"]) + float(info["used_cpu_sys"])


async def measure(
    limiter: Limiter, requests: int, concurrency: int, redis: Redis | None = None
) -> dict[str, float]:
    """Run ``requests`` checks across ``concurrency`` coroutines; return latency stats.

    Also reports how busy this Python process and (if given) the Redis server were, as a
    percentage of one CPU core, to show which side is the bottleneck.
    """
    latencies: list[int] = []
    cpu_before, redis_before = _process_cpu_seconds(), await _redis_cpu_seconds(redis)

    async def worker(offset: int) -> None:
        for i in range(offset, requests, concurrency):
            start = time.perf_counter_ns()
            await limiter.check(KEYS[i % len(KEYS)])
            latencies.append(time.perf_counter_ns() - start)

    began = time.perf_counter()
    await asyncio.gather(*(worker(i) for i in range(concurrency)))
    elapsed = time.perf_counter() - began
    python_cpu = _process_cpu_seconds() - cpu_before
    redis_cpu = await _redis_cpu_seconds(redis) - redis_before
    cuts = statistics.quantiles(latencies, n=100, method="inclusive")
    return {
        "p50_us": cuts[49] / 1000,
        "p95_us": cuts[94] / 1000,
        "p99_us": cuts[98] / 1000,
        "throughput_per_s": requests / elapsed,
        "python_cpu_pct": 100 * python_cpu / elapsed,
        "redis_cpu_pct": 100 * redis_cpu / elapsed,
    }


async def bench_backend(
    name: str, backend: Backend, requests: int, concurrency: int, redis: Redis | None = None
) -> list[dict[str, Any]]:
    """Every algorithm on one backend, sequential then concurrent."""
    rows = []
    for algorithm in ALGORITHMS.values():
        limiter = Limiter(algorithm, QUOTA, backend, name=f"bench-{name}")
        await measure(limiter, 1000, 1)  # warm up: script load, connections, caches
        for mode, workers in (("sequential", 1), ("concurrent", concurrency)):
            stats = await measure(limiter, requests, workers, redis)
            rows.append({"backend": name, "algorithm": algorithm.name, "mode": mode, **stats})
            print(f"{name:6} {algorithm.name:16} {mode:10} {stats}")
    return rows


def to_markdown(rows: list[dict[str, Any]], concurrency: int) -> str:
    """The README table: latency from the sequential run, throughput from both."""
    by_key = {(r["backend"], r["algorithm"], r["mode"]): r for r in rows}
    lines = [
        "| Backend | Algorithm | p50 (µs) | p95 (µs) | p99 (µs) | Throughput, 1 coroutine "
        f"(checks/s) | Throughput, {concurrency} coroutines (checks/s) |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    cpu_lines = [
        "",
        f"CPU busy during the {concurrency}-coroutine run (% of one core):",
        "",
        "| Backend | Algorithm | Python process | Redis server |",
        "| --- | --- | ---: | ---: |",
    ]
    for (backend, algorithm, mode), seq in by_key.items():
        if mode != "sequential":
            continue
        conc = by_key[(backend, algorithm, "concurrent")]
        lines.append(
            f"| {backend} | {algorithm} | {seq['p50_us']:.1f} | {seq['p95_us']:.1f} | "
            f"{seq['p99_us']:.1f} | {seq['throughput_per_s']:,.0f} | "
            f"{conc['throughput_per_s']:,.0f} |"
        )
        redis_cpu = f"{conc['redis_cpu_pct']:.0f}%" if backend == "redis" else "n/a"
        cpu_lines.append(
            f"| {backend} | {algorithm} | {conc['python_cpu_pct']:.0f}% | {redis_cpu} |"
        )
    return "\n".join(lines + cpu_lines) + "\n"


async def main() -> None:
    """Run the benchmark and write ``bench/results/latency.{json,md}``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=20_000)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument(
        "--redis-url", default=os.environ.get("REDIS_URL", "redis://localhost:6379/15")
    )
    args = parser.parse_args()

    rows = await bench_backend("memory", MemoryBackend(), args.requests, args.concurrency)
    pool = BlockingConnectionPool.from_url(args.redis_url, max_connections=64, timeout=10)
    client = Redis(connection_pool=pool)
    await client.flushdb()
    info = await client.info("server")
    rows += await bench_backend(
        "redis", RedisBackend(client), args.requests, args.concurrency, client
    )
    await client.flushdb()
    await client.aclose()

    meta = describe(
        requests=args.requests,
        concurrency=args.concurrency,
        quota=f"{QUOTA.limit}/{QUOTA.period}s over {len(KEYS)} keys",
        redis_version=info["redis_version"],
        redis_location="same machine (loopback)",
    )
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "latency.json").write_text(json.dumps({"meta": meta, "rows": rows}, indent=2))
    (RESULTS / "latency.md").write_text(to_markdown(rows, args.concurrency))
    print(to_markdown(rows, args.concurrency))


if __name__ == "__main__":
    asyncio.run(main())
