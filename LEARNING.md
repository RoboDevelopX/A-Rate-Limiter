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
