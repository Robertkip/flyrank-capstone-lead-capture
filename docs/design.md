# Design — Embeddable Widget & Lead-Capture Platform

One page, written before the build. Phase 1 gate of the capstone brief.

## Problem

A customer defines a widget (signup form, CTA, popover) in an authenticated
admin API, pastes one `<script>` tag onto any website they own, and receives
the resulting leads in a dashboard. The submission endpoint is reachable by the
entire public internet: any origin, any payload, any volume, no trust.

## Explicit non-goal

**No visual form builder, and no hosted customer-facing frontend.** Widgets are
defined through the JSON API only. The owner "dashboard" is a set of JSON
endpoints, not a UI. The grade lives in the backend; a form-builder is a
different product.

Two smaller non-goals worth stating: no real CDN or custom domain (the bundle
is served by the API with CDN-shaped cache headers), and no distributed rate
limiting (single-process, in-memory — see Limitations in the README).

## Data model

```
tenants     id, email (unique), name, password_hash, created_at

widgets     id, tenant_id -> tenants, public_id (unique, 16 hex chars),
            name, type, title, description, button_text, success_message,
            fields JSONB, display JSONB, allowed_origins JSONB,
            notify_email, notify_webhook_url,
            active, config_version, created_at, updated_at

submissions id, widget_id -> widgets, tenant_id -> tenants,
            data JSONB, ip, user_agent, referer, origin,
            geo_status, geo_provider, country, country_code, region, city,
            idempotency_key, created_at

outbox_     id, submission_id -> submissions, event_type, payload JSONB,
events      status, attempts, max_attempts, next_attempt_at, last_error,
            created_at, delivered_at

spam_events id, widget_id, tenant_id, reason, ip, created_at
```

Two decisions worth defending:

- **`submissions.tenant_id` is denormalised** even though it is reachable via
  `widget_id`. Every dashboard query filters on it directly, so isolation is one
  indexed predicate rather than a join the next developer might forget to write.
- **`widgets.public_id` is separate from `widgets.id`.** The public handle
  appears in every embed snippet on the open web; the primary key does not.
  Rotating one never touches the other.

Indexes: `widgets(public_id)` unique, `widgets(tenant_id, created_at)`,
`submissions(widget_id, created_at)`, `submissions(tenant_id, created_at)`,
`outbox_events(status, next_attempt_at)`, and a unique constraint on
`submissions(widget_id, idempotency_key)`.

## The embed flow

```
owner  ──POST /api/widgets──▶ widget row + public_id
                             ◀── embed_snippet

customer site (any origin)
   <script src="{API}/embed/widget.v1.js?id={public_id}">
       │  bundle: Cache-Control public, max-age=1y, immutable + ETag
       ▼
   GET /api/public/widgets/{public_id}/config
       │  config: Cache-Control public, max-age=60, ETag "{id}-v{version}"
       ▼
   render form into the host page

visitor submits
   POST /api/public/submissions   (CORS, preflighted)
```

The bundle URL carries its version, so it is cached forever and a release
changes the URL rather than expiring a cache. The config is short-lived and
carries an ETag built from `config_version`, so editing a widget invalidates it
immediately while unchanged configs answer `304`.

## API surface

Three request paths, deliberately kept separate.

**Owner, authenticated** (Bearer JWT, tenant scoped):

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/register`, `/api/auth/login` | issue a token |
| GET | `/api/auth/me` | current tenant |
| POST/GET/PATCH/DELETE | `/api/widgets[/{id}]` | widget CRUD |
| GET | `/api/widgets/{id}/embed` | the snippet |
| GET | `/api/dashboard/summary` | totals, rates, per-widget counts |
| GET | `/api/dashboard/submissions` | paginated leads |
| GET | `/api/dashboard/widgets/{id}/stats` | by-day and by-country buckets |

**Customer site, public and cached:**

| Method | Path | Cache |
|---|---|---|
| GET | `/embed/widget.{version}.js` | `max-age=31536000, immutable` |
| GET | `/api/public/widgets/{public_id}/config` | `max-age=60` + ETag |

**Visitor, public and hardened:**

| Method | Path |
|---|---|
| POST | `/api/public/submissions` |
| OPTIONS | `/api/public/submissions` (preflight) |

## The submission path, in order

```
validate payload shape        Pydantic, extra="forbid"     → 422
  ↓
resolve widget by public_id                                 → 404
  ↓
per-widget origin allow-list                                → 403
  ↓
rate limit (per IP, then per widget)                        → 429
  ↓
spam classify (honeypot, fill-time)          → 201, silently dropped
  ↓
validate against this widget's declared fields              → 422
  ↓
enrich: provider A → provider B → "unavailable"   (never raises)
  ↓
store submission + queue outbox events   (one transaction)
  ↓
201 { status, id, message }
```

Cheap rejections come first. Enrichment sits after every gate so a flood cannot
turn into outbound HTTP load.

## Layering

```
routers/    HTTP only: parse, authorise, set headers, choose status codes
services/   business logic, tenant scoping, transactions
models/db   persistence, migrations, indexes
```

Cross-cutting units, each independently testable: `ratelimit`, `spam`,
`enrichment/` (providers + chain), `outbox/` (worker + handlers), `security`,
`errors`.

## Error handling

Every client-caused failure returns `{"error": ..., "detail": ...}` with a 4xx.
A 500 means a bug, never bad input. Three handlers cover it: `AppError` (domain
errors carry their own status), `RequestValidationError` (422 with a per-field
list), and `StarletteHTTPException`. Oversized bodies are refused with 413 by
middleware, before any parser sees them.

## Degradation boundaries

The two dependencies that can be down are handled in opposite ways, for the
same reason — neither may destroy a lead.

- **Enrichment is absorbed.** `enrich_ip` catches everything, including a
  provider raising something unplanned, and returns `("unavailable", None)`. It
  has no failure mode that reaches the caller.
- **Side effects are deferred.** The request path never sends an email or calls
  a webhook; it writes an `outbox_events` row in the *same transaction* as the
  submission. A background worker claims rows with `FOR UPDATE SKIP LOCKED`,
  retries with exponential backoff, and dead-letters with an alert log after
  `max_attempts`. If the row is stored the effect is queued; if it is not,
  nothing is queued.

## Idempotency

Two independent layers:

- A client may send `Idempotency-Key` on a submission. A replay returns the
  originally stored row, enforced by a unique constraint on
  `(widget_id, idempotency_key)` — so a race loses at the database, not in
  application logic.
- `FOR UPDATE SKIP LOCKED` means one outbox event is claimed by exactly one
  worker, so the confirmation email is sent once even with several workers.

## Testing strategy

Deterministic, no network. Mock geo providers toggled by config prove the
fallback chain; `SIDE_EFFECT_FORCE_FAIL` proves the side-effect boundary. Tests
run against real Postgres so JSONB, indexes and `SKIP LOCKED` behave as they do
in production. `scripts/probes.sh` runs the brief's six acceptance probes
against a live stack.
