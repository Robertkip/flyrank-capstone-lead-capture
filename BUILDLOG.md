# BUILDLOG

Honest record of how this capstone was built, including where AI helped, where
it was wrong, and what changed as a result. The brief asks for honesty, not
perfection.

## How AI was used

This project was built in a pair-programming session with Claude (Claude Code).
The workflow was: I chose the stack and the architectural constraints, the
assistant drafted implementations, and every piece was run against a live stack
and a real Postgres before it counted as done. Nothing in this repository is
code that was never executed.

Decisions I made, not the AI:

- **Python + FastAPI** over Node/Express.
- **Postgres via Docker Compose** over SQLite, so `docker compose up` is the one
  documented run command and the schema is real (JSONB, partial indexes,
  `SKIP LOCKED`).
- **JWT register/login** over static seeded API keys, because tenant isolation is
  only convincingly demonstrated with two accounts that actually authenticate.

Decision the assistant recommended and I accepted:

- **The transactional outbox** for side effects, rather than
  `BackgroundTasks`. The argument that won: it is the only option that also
  satisfies shared requirements #3 (background job with retries and a failure
  alert) and #5 (idempotency), with no extra infrastructure. `BackgroundTasks`
  would have proven "failure doesn't block success" and nothing else.

## Where the AI was wrong

These are the things that did not work first time. They are listed because they
are the interesting part.

### 1. The rate limiter froze its configuration at import time

The first limiter read `settings.rate_limit_ip_per_minute` in its constructor
and stored the integer. The module-level `limiter` object is built at import, so
the limit was fixed the moment the process started and no later change could
affect it.

Two tests caught this — they set a low limit and then watched the limiter
cheerfully apply the default. The failure was easy to dismiss as "just a test
setup problem", which is exactly what it was not: it meant the limiter's
configuration could never be changed after start-up, and it made the component
untestable in isolation.

**Fix:** `SlidingWindowLimiter` now accepts an `int` *or* a zero-argument
callable and resolves the limit on every check. While fixing it I also found a
second, subtler bug: with both buckets allowing a request, the endpoint reported
whichever decision it checked last, so `X-RateLimit-Remaining` could show the
widget bucket's roomy number while the IP bucket was nearly exhausted. It now
reports the bucket the caller is closest to exhausting.

### 2. An edit that silently orphaned an exception handler

While improving the validation error message, a helper function was inserted at
module level *in the middle of* `register_error_handlers`. Python accepted it —
the file parsed fine — but everything after the insertion point, including the
`StarletteHTTPException` handler, was now dead code outside the function.

Nothing failed loudly. I caught it by reading the file after the edit rather
than trusting that a successful edit meant a correct edit. The lesson I'd repeat:
after an insertion into a nested scope, read the surrounding block.

**Fix:** rewrote `app/errors.py` with the helper at module scope, above the
registration function.

### 3. `curl -I` revealed a missing HEAD handler

While capturing cache-header evidence, `curl -I` returned `405 Method Not
Allowed` on the bundle and config endpoints. `curl -I` sends HEAD, and FastAPI's
`@router.get` registers GET only.

This was not a test artifact. Caches, CDNs and proxies routinely probe assets
with HEAD, and these are precisely the two endpoints designed to sit behind a
cache. Returning 405 to them is a real defect in an asset-serving path.

**Fix:** both routes are now `api_route(..., methods=["GET", "HEAD"])`, with a
test asserting HEAD returns 200 and the correct cache headers.

### 4. Byte offsets leaking into error messages

Malformed JSON produced `{"field": "30", ...}` — Pydantic's `loc` for a JSON
decode error is a byte offset, and the generic field-path formatter happily
rendered it as a field name. Harmless, but it is the kind of detail that makes an
API feel unfinished to whoever integrates with it.

**Fix:** `field_name()` special-cases `json_invalid` and returns `"body"`, with a
regression test.

### 5. Ports assumed to be free — and one conflict killing the whole stack

The first `docker compose up` failed: port 5500 was already bound on this
machine (VS Code's Live Server), and 8025 and 5433 were too. The compose file had
hard-coded host ports.

The first fix was to make all four ports `${VAR:-default}`. But when I later
tested the *stranger* path — `cp .env.example .env` and nothing else — I found
the more serious problem: Docker aborts the **entire** `compose up` over a single
port conflict. A stray process on 5500, which only serves a static demo page,
stopped the API from starting at all, and the only explanation Docker gave was a
wall of text ending in `address already in use`.

That is a direct hit on the brief's "a stranger can run it" rule, and I would not
have found it by assuming the README was accurate.

**Fix:** `scripts/preflight.sh` checks all four ports, names the process holding
each conflict, and prints the exact `sed` command to fix it. Verified by running
it against the real conflicts on this machine, applying its own suggestions, and
booting cleanly.

### 6. Two providers of live geo code that had never actually run

`GEO_MODE=live` had been written but never executed — the whole test suite used
the mock providers. Running it for real showed ip-api.com working correctly
(Ashburn, US), and ipapi.co returning `429` from its free tier.

The chain degraded exactly as designed, which was reassuring. But it meant
provider B's *success*-parsing path was code that had never once run — and it
maps different key names than provider A (`country_name`/`region` versus
`countryCode`/`regionName`), which is precisely the kind of thing that fails
silently.

**Fix:** nine HTTP-level tests in `tests/test_live_providers.py` using both APIs'
real payload shapes, mocked with `respx` so they are deterministic and cannot go
flaky when a free tier rate-limits. They also cover the shared trap that both
APIs signal some failures with a `200` carrying an error in the body.

## What I verified rather than assumed

- Every one of the 79 tests runs against real Postgres, not SQLite — so JSONB,
  the unique constraint behind idempotency, and `FOR UPDATE SKIP LOCKED` are the
  real implementations, not approximations.
- The dead-letter path was watched end to end in the logs: attempts at 2s, 4s,
  8s, 16s, then an `ALERT` line, with the submission row confirmed intact in SQL
  afterwards.
- The widget was loaded and submitted **in an actual browser** on
  `localhost:5501` against the API on `localhost:8000`, and the resulting row
  recorded `origin = http://localhost:5501`. A cross-origin `curl` would not have
  proven this — curl does not enforce CORS; only a browser does.
- One outbox event in the evidence delivered on attempt 4: it was queued during
  the forced-failure window and succeeded once the dependency recovered. That
  wasn't staged, and it's better proof of the retry policy than the test is.

## Things I chose not to build

- No form builder UI, and no dashboard frontend. Written down as an explicit
  non-goal in `docs/design.md` before starting, so it stayed a decision rather
  than becoming an excuse.
- No Redis. The rate limiter is per-process and in-memory, which is documented
  as a limitation in the README rather than hidden. The storage sits behind one
  class, so swapping it is a contained change.
- No stretch goals. The brief is explicit that a finished core beats scattered
  half-stretches, and I agree with the reasoning.

## Code I can explain

The brief warns that "the AI wrote it" is not an answer. The parts most worth
asking me about, and where they live:

- Why the outbox row is written in the *same transaction* as the submission, and
  what breaks if it isn't — `app/services/submission_service.py`.
- Why `enrich_ip` catches bare `Exception` when it already catches
  `ProviderError` — `app/enrichment/chain.py`.
- Why `allow_credentials=False` makes `allow_origins=["*"]` safe here, and what
  would change if auth moved to cookies — `app/main.py`.
- Why another tenant's widget returns 404 rather than 403 —
  `app/services/widget_service.py`.
- Why `SKIP LOCKED` is what makes the worker idempotent —
  `app/outbox/worker.py`.
