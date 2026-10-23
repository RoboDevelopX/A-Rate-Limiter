"""How closely each algorithm enforces "limit per period", and what a key costs in Redis.

Accuracy: the pure algorithms are driven with a simulated clock (deterministic, seeded),
and we record the most units admitted inside any trailing window of one period, divided
by the limit. 1.00 means the limit was never exceeded in any window; 2.00 means twice the
limit got through within one period. Two workloads:

- boundary burst: the full limit just before a window boundary, then again just after;
- random bursts: one hour of bursty traffic averaging well above the limit.

Memory: Redis ``MEMORY USAGE`` of one key after 100 allowed requests.

Usage: ``python -m bench.accuracy [--redis-url URL]``
"""

import argparse
import asyncio
import json
import os
import random
from pathlib import Path
from typing import Any

from redis.asyncio import Redis

from bench.environment import describe
from mlratelimit import Limiter, Quota, RedisBackend
from mlratelimit.algorithms import ALGORITHMS, Algorithm

RESULTS = Path(__file__).parent / "results"
QUOTA = Quota(limit=100, period=60)
START = 1_800_000_000.0  # a multiple of 60, so windows start on the minute


def boundary_burst() -> list[float]:
    """100 requests at 59.9 s and 100 more at 60.0 s."""
    return [START + 59.9] * 100 + [START + 60.0] * 100


def random_bursts(seed: int = 42, seconds: int = 3600) -> list[float]:
    """Each second, a 10% chance of a burst of 5-50 requests spread over that second."""
    rng = random.Random(seed)
    times: list[float] = []
    for second in range(seconds):
        if rng.random() < 0.1:
            times += sorted(START + second + rng.random() for _ in range(rng.randint(5, 50)))
    return times


def admitted(algorithm: Algorithm[Any], arrivals: list[float]) -> list[float]:
    """Times of the requests the algorithm allowed (state kept as a backend would)."""
    state: Any = None
    allowed = []
    for now in arrivals:
        evaluation = algorithm.evaluate(state, now, QUOTA, 1)
        if evaluation.result.allowed:
            state = evaluation.commit()
            allowed.append(now)
    return allowed


def peak_in_window(times: list[float], period: float) -> int:
    """Most timestamps inside any half-open window (t - period, t]."""
    peak, left = 0, 0
    for right, now in enumerate(times):
        while times[left] <= now - period:
            left += 1
        peak = max(peak, right - left + 1)
    return peak


async def redis_bytes_per_key(client: Redis, algorithm: Algorithm[Any]) -> int:
    """Redis memory for one key after 100 allowed requests."""
    limiter = Limiter(algorithm, Quota(limit=1000, period=60), RedisBackend(client), name="mem")
    for _ in range(100):
        await limiter.check("caller")
    usage = await client.memory_usage(f"rl:{{caller}}:mem:{algorithm.name}")
    return int(usage or 0)


async def main() -> None:
    """Run the comparison and write ``bench/results/accuracy.{json,md}``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--redis-url", default=os.environ.get("REDIS_URL", "redis://localhost:6379/15")
    )
    args = parser.parse_args()
    client = Redis.from_url(args.redis_url)
    await client.flushdb()

    workloads = {"boundary_burst": boundary_burst(), "random_bursts": random_bursts()}
    exact = {name: len(admitted(ALGORITHMS["sliding_log"], w)) for name, w in workloads.items()}
    rows = []
    for algorithm in ALGORITHMS.values():
        row: dict[str, Any] = {"algorithm": algorithm.name}
        for name, arrivals in workloads.items():
            allowed = admitted(algorithm, arrivals)
            row[f"{name}_peak_ratio"] = peak_in_window(allowed, QUOTA.period) / QUOTA.limit
            row[f"{name}_admitted_vs_exact"] = len(allowed) / exact[name]
        row["redis_bytes_after_100"] = await redis_bytes_per_key(client, algorithm)
        rows.append(row)
        print(row)
    await client.flushdb()
    await client.aclose()

    meta = describe(
        quota=f"{QUOTA.limit} per {QUOTA.period:.0f}s",
        random_bursts_requests=len(workloads["random_bursts"]),
    )
    lines = [
        "| Algorithm | Peak in any window / limit, boundary burst | Peak / limit, random "
        "bursts | Admitted vs exact log, random bursts | Redis bytes per key (100 requests) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    lines += [
        f"| {r['algorithm']} | {r['boundary_burst_peak_ratio']:.2f} | "
        f"{r['random_bursts_peak_ratio']:.2f} | {r['random_bursts_admitted_vs_exact']:.2f} | "
        f"{r['redis_bytes_after_100']:,} |"
        for r in rows
    ]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "accuracy.json").write_text(json.dumps({"meta": meta, "rows": rows}, indent=2))
    (RESULTS / "accuracy.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
