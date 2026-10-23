# LEARNING.md

Short notes on the backend and infrastructure concepts this project uses, written for someone
coming from ML. Each milestone adds a few.

## Milestone 1: project skeleton, tooling, CI

**Lockfiles and reproducible installs.** `pyproject.toml` says which packages you need and which
version ranges are acceptable; `uv.lock` records the exact versions that were resolved. Installing
with `uv sync --locked` on your laptop, in CI, and in Docker gives the same versions everywhere,
so "works on my machine" bugs from silent upgrades go away. It is the same idea as pinning a
`requirements.txt`, but generated and checked automatically.

**Continuous integration (CI).** CI is a machine that runs your checks on every push and pull
request, so a broken change is caught before it is merged rather than after. Here GitHub Actions
runs two jobs in parallel: lint plus type check, and tests. The test job starts a real Redis as a
*service container*: a Docker container that lives next to the job for its duration, reachable on
`localhost:6379`, then thrown away.

**Strict type checking.** `mypy --strict` refuses untyped functions and implicit `Any`. In a
library this matters more than in a notebook: callers rely on the signatures, and a type checker
catches a wrong argument or a forgotten `None` case before any test runs. Think of it as a
compile step for Python.

**Linting vs formatting.** A formatter (`ruff format`) only changes layout, so code reviews never
argue about spacing. A linter (`ruff check`) finds likely bugs and bad patterns: unused imports,
mutable default arguments, `except:` that swallows `KeyboardInterrupt`, `assert` in production
code. Both run in CI, so the main branch stays clean without anyone policing it by hand.

## Milestone 2: interface, clock, in-memory backend, first two algorithms

**The check-then-act race.** A rate limiter reads a counter, decides, then writes. If two
requests both read "1 left" before either writes, both are allowed and the limit is broken.
This is the core correctness problem of the whole project; every backend must make
read-decide-write one indivisible (atomic) step. `test_many_coroutines_on_one_key_never_exceed_the_limit`
fires 1,000 concurrent checks at one key and asserts exactly `limit` succeed.

**Why the in-memory backend needs no lock.** asyncio is cooperative: a coroutine only gives up
the event loop at an `await`. `MemoryBackend.apply` has no `await` between reading and writing
state, so no other coroutine can run in the middle, and the race above cannot happen. This
guarantee is per event loop: threads or multiple worker processes each have their own memory
and would each allow the full limit, which is why multi-worker deployments need Redis.

**Lazy refill instead of timers.** A token bucket never runs a background job that adds tokens.
It stores `(tokens, updated_at)` and, on the next check, adds `elapsed * rate` tokens, capped at
capacity. Idle keys cost zero CPU, and the stored state can be deleted once the bucket would be
full again, because a full bucket is indistinguishable from a brand new key.

**Fixed window's boundary burst.** Windows are aligned to the clock (e.g. 10:00:00-10:00:59), and
the counter resets at each boundary. A client can spend the whole limit at 10:00:59.5 and again
at 10:01:00.0, so twice the limit passes in half a second. It is cheap (one counter) and
predictable, but this burst is why smoother algorithms exist. A test reproduces it on purpose.

**Injectable clocks and clock skew.** Every algorithm takes `now` from a `Clock` instead of calling
`time.time()` itself, so tests move time with `ManualClock.advance()` and never sleep. Wall clocks
can also jump backwards (NTP corrections), so the algorithms clamp: elapsed time is never
negative and a window never moves backwards, which means a skewed clock cannot hand out
extra quota.

## Milestone 3: the remaining algorithms and property-based tests

**Sliding log vs sliding counter: exactness vs memory.** The sliding log stores a timestamp for
every allowed request and counts those in the trailing window, so it is exact at every instant
but its memory grows with the limit. The sliding counter keeps only two numbers (this window's
count and the previous one's) and estimates `previous * (fraction of the previous window still
in view) + current`. That assumes the previous window's traffic was evenly spread, so it can
over- or under-admit for bursty traffic, but it fixes most of fixed window's boundary burst at
O(1) cost. Pick the log for low, strict limits; the counter for high-volume ones.

**Token bucket vs leaky bucket.** A token bucket fills with tokens and requests take them out; a
leaky bucket fills with requests and drains at a constant rate. Used as a *meter* (reject when
full) the two are the same machine upside down: `level = capacity - tokens`. The property test
`test_leaky_bucket_decides_exactly_like_token_bucket` proves they make identical decisions. The
real difference only appears in the *queue* variant, where a leaky bucket delays requests to
smooth the output rate, which suits network shaping more than an API that must answer quickly.
(GCRA, used by some Redis modules, is the same meter stored as one timestamp; it is not used here
because adding fractional intervals to epoch-sized floats loses precision.)

**Property-based testing.** Instead of hand-picking inputs, hypothesis generates hundreds of random
traffic patterns per test and checks an invariant on each, e.g. "the sliding log never allows
more than the limit in any trailing window" or "retrying after `retry_after` succeeds and
retrying 1 ms earlier fails". When it finds a failure it shrinks the input to the smallest
counter-example. To check the tests have teeth, I injected four small bugs (an off-by-a-bit
expiry, a wrong retry formula, an over-full bucket, a short TTL); each was caught.

**Expiry must be invisible.** Every algorithm reports `expires_at`, the moment its stored state
carries no information. A property test runs each traffic pattern twice, once deleting state at
`expires_at` and once keeping it forever, and asserts every decision is identical. That is what
makes it safe to use Redis key TTLs (next milestone) to bound memory.

**Floating point at epoch scale.** Unix timestamps around 1.7e9 leave a double only about 0.24 µs
of resolution, so `now + 1/3` is not exact. The property tests use times that are multiples of
1/64 s and power-of-two periods so the math is exact and invariants can be asserted with no
tolerance; production traffic only sees sub-microsecond rounding.

## Milestone 4: Redis backend with Lua scripts

**Why a Lua script makes a check atomic.** Redis executes commands one at a time on a single
thread, and a Lua script counts as one command: while it runs, no other client's command can
execute. So read state, decide, write state happens as one indivisible step even when 50 app
servers hit the same key. `test_naive_read_modify_write_overadmits` shows the alternative failing:
a plain `GET`, decide in Python, `SET` lets far more than the limit through, while the scripted
version admits exactly the limit for all five algorithms. (The other classic tool, `WATCH` plus
`MULTI`, is optimistic: it retries on conflict, which melts down on a hot key.)

**EVALSHA and the script cache.** Sending the whole script on every request wastes bandwidth, so
Redis caches scripts by their SHA1 hash and clients call `EVALSHA <sha>`. A restart, failover or
`SCRIPT FLUSH` empties that cache; Redis then answers `NOSCRIPT`, and redis-py's `Script` object
reloads the source and retries once. A test flushes the cache mid-run to prove it.

**Server time, not client time.** Each app server's clock drifts a little. If every server passed
its own `time.time()`, two servers disagreeing by 300 ms would compute different windows and
refills for the same key. The scripts read Redis's own clock with `TIME`, so there is one clock
for everyone. Tests can still inject a fixed time, which is how the Lua and Python
implementations are compared step by step.

**Floats across the Lua boundary.** Redis converts Lua numbers to *integers* in replies and to
14-significant-digit strings in commands, so `3.123456789012345` tokens would silently become
`3.1234567890123`. Every float is therefore formatted with `%.17g` (enough digits to round-trip a
double) before it leaves Lua. Swapping that for `tostring` makes the parity test fail, which is
how we know the test would catch the bug.

**TTLs keep Redis memory bounded.** Every key gets a TTL equal to its logical expiry (when its
state stops mattering) plus a small padding. Because the previous milestone proved that
forgetting expired state never changes a decision, Redis can delete keys on its own and memory
only holds callers active within roughly one period.

## Milestone 5: FastAPI integration, config, keys, failure policy, ML features, metrics

**Fail-open vs fail-closed.** When Redis is unreachable the limiter cannot know the answer, so
it must pick one. Fail-open allows the request: the API stays up but is briefly unprotected,
which suits most product APIs. Fail-closed rejects it: nothing gets through unmetered, which
suits expensive GPU endpoints or abuse limits. Either way the check must fail *fast*, so every
backend call has a 50 ms timeout instead of hanging a request thread on a dead socket.

**Circuit breakers.** If Redis is down, waiting 50 ms on every request still adds latency and
piles load onto a struggling server. A circuit breaker counts consecutive failures; after 5 it
"opens" and skips Redis entirely for a few seconds (instant fallback), then lets one probe
request through ("half-open"). If the probe succeeds the breaker closes and normal service
resumes. The `ratelimit_backend_errors_total{reason="circuit_open"}` metric shows when it trips.

**Cost-based limits for LLMs.** One request to a 70B model generating 2,000 tokens costs far more
GPU time than a 10-token request, so counting requests alone is unfair and unsafe. Each tier has
a requests-per-minute *and* a tokens-per-minute quota, checked atomically together. The token cost
is estimated before inference (prompt length / 4 + `max_tokens`) because the true count is only
known afterwards; a rejected request charges neither quota.

**Rate limits vs concurrency limits.** A rate limit bounds how many requests start per minute; a
concurrency limit bounds how many run at the same time. GPU endpoints need both: 20 requests a
minute is fine, but 20 at once can run a GPU out of memory. Concurrency slots are leases in a
Redis sorted set that expire on their own, so a crashed worker cannot hold a slot forever. When
slots run out the API answers 503 (server busy), not 429 (your quota), and takes the slot before
charging quota so a busy server does not burn the caller's budget.

**Trusting X-Forwarded-For.** Behind a load balancer every request's TCP peer is the balancer, so
the real client IP comes from the `X-Forwarded-For` header. But any client can send that header,
so it is only read when the peer is a configured trusted proxy, and then from the right: each
proxy appends the address it saw, so the right-most address that is not one of our proxies is
the one our own infrastructure vouches for. Everything to its left could be forged.

## Milestone 6: Docker, benchmarks, documentation

**Multi-stage Docker builds.** The `Dockerfile` installs dependencies in a *build* stage (with
uv and a compiler toolchain available), then copies only the finished virtualenv into a clean
*runtime* stage. The final image has no build tools, runs as a non-root user, and has a
`HEALTHCHECK` that calls `/healthz`. Compose waits for Redis to be healthy before starting the
API (`depends_on: condition: service_healthy`), so the app never starts against a Redis that
is not ready yet.

**Percentiles, not averages.** One slow check in a hundred barely moves the mean, but if an API
call makes several backend calls, or a page makes dozens of API calls, users hit that "rare" slow
path all the time. That is why the benchmark reports p50, p95 and p99 (the latency 50%, 95% and
99% of calls beat), measured per call after a warm-up so one-time costs like loading scripts are
excluded.

**Find the bottleneck before optimizing.** The Redis numbers looked slow (~9,000 checks/s) until
the benchmark also recorded CPU: the Python process was at ~100% of a core while Redis sat at
~30%. So the limit is client-side work in one Python process (building commands, parsing
replies), not Redis, and the fix is more worker processes, not a faster Redis. Measure, then
decide.

**Honest benchmarks.** Numbers only mean something with their context: hardware, versions,
method, and what is excluded (here, network latency, because Redis ran on the same machine).
Cloud VMs are noisy, so the README says how much two runs differed. Raw results and machine
details are saved as JSON next to the tables, and `make bench` regenerates them.

**Smoke tests.** Unit tests prove the parts work; a smoke test proves the assembled product
works. CI builds the real Docker image, starts the Compose stack, and runs `scripts/smoke.sh`:
five calls succeed, the sixth gets a 429, and `/metrics` recorded it. That catches things unit
tests cannot, such as a missing file in the image or a wrong port.
