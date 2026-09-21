# EVIDENCE

One pasted proof per requirement checkbox from Section 6 of the brief, plus the
six acceptance probes from Section 13. Every transcript below is real output
captured from a running stack — nothing here is hand-written.

**Reproduce all of it:**

```bash
docker compose up -d
docker compose exec -T db psql -U leadcapture -d postgres -c "CREATE DATABASE leadcapture_test;"
docker compose exec api pytest -q      # 79 passed in 46.12s
./scripts/probes.sh                    # the six acceptance probes
```

Summary: **79 passed in 46.12s**, and **23 probe assertions passed, 0 failed**.

---

## The six acceptance probes

Full transcript from `./scripts/probes.sh`.

### PROBE 1 — a valid cross-origin submission is stored, 2xx, and visible on the dashboard

```
----------------
  content-type: application/json
  access-control-allow-origin: *
  PASS  submission accepted (got 201)
  PASS  visible on dashboard (got 1)
  PASS  correct row returned (got probe1@example.com)
  enriched country: Kenya
```

The page origin is `http://localhost:5501`, the API origin is
`http://localhost:8000` — genuinely different origins.

### PROBE 2 — malformed and oversized payloads produce clean 4xx JSON, never a 500

```
----------------
-- malformed JSON:
  {"error":"validation_failed","detail":[{"field":"body","message":"JSON decode error","type":"json_invalid"}]}
  PASS  malformed JSON rejected (got 422)
-- missing required field:
  {"error":"validation_failed","detail":[{"field":"email","message":"this field is required"}]}
  PASS  missing required field rejected (got 422)
-- unknown field:
  {"error":"validation_failed","detail":[{"field":"is_admin","message":"unknown field for this widget"}]}
  PASS  unknown field rejected (got 422)
-- oversized payload:
  {"error":"payload_too_large","detail":"body must be at most 8192 bytes"}
  PASS  oversized payload rejected (got 413)
```

### PROBE 3 — a burst produces 429s, and normal traffic keeps working

```
----------------
  40 rapid submissions -> 201 x27, 429 x13
  PASS  429s appeared under burst
  PASS  API still serving other traffic (got 200)
  PASS  config endpoint unaffected (got 200)
```

### PROBE 4 — geo provider fallback chain

```
----------------
-- both providers up:
  PASS  provider A enriched (got mock-provider-a)
-- provider A disabled:
  PASS  provider B took over (got mock-provider-b)
  country now: Germany
-- both providers disabled:
  PASS  submission still succeeds (got 201)
  PASS  stored without geo (got unavailable)
  PASS  the lead itself survived (got geo-none@example.com)
```

Provider A answers with Kenya; with A disabled, provider B answers with Germany;
with both disabled, the submission is still stored and the lead itself survives.

### PROBE 5 — a failing side effect never blocks a submission

```
----------------
  {"status":"ok","id":"a27b2f54-0be5-46ac-9467-396d965b4e1a","message":"Thanks! We'll be in touch."}
  PASS  submission returned success (got 201)
  PASS  row is stored (got side-effect@example.com)
  worker log (side effect failing and retrying in the background):
    api-1  | 2026-08-29 05:38:36,118 WARNING app.outbox.worker outbox.retry event_id=9c590139-7756-4176-9bf0-3f532f92878d type=submission.email attempt=1/5 next_attempt=2026-08-29T05:38:38.118205+00:00 error=SideEffectError: SIDE_EFFECT_FORCE_FAIL=true — deliberate side-effect failure
    api-1  | 2026-08-29 05:38:38,138 WARNING app.outbox.worker outbox.retry event_id=9c590139-7756-4176-9bf0-3f532f92878d type=submission.email attempt=2/5 next_attempt=2026-08-29T05:38:42.138496+00:00 error=SideEffectError: SIDE_EFFECT_FORCE_FAIL=true — deliberate side-effect failure
```

### PROBE 6 — a filled honeypot is silently dropped

```
----------------
  {"status":"ok","id":null,"message":"Thanks! We'll be in touch."}
  PASS  bot sees an ordinary success (got 201)
  PASS  nothing was stored (got 31)
  PASS  counted as spam_blocked (1)
```

The bot receives byte-for-byte the response a real visitor gets (`"status":"ok"`
and the widget's success message), but `id` is `null`, nothing is stored, and the
owner's dashboard counts it as spam.

---

## Requirements checklist (Section 6)

### Widget management

#### ☑ Authenticated CRUD endpoints for widgets; requests without valid auth are rejected

```
tests/test_auth_and_tenancy.py::test_register_then_login_issues_a_token PASSED
tests/test_auth_and_tenancy.py::test_duplicate_email_is_rejected PASSED
tests/test_auth_and_tenancy.py::test_wrong_password_is_401_with_a_generic_message PASSED
tests/test_auth_and_tenancy.py::test_widget_routes_reject_missing_and_invalid_auth PASSED
tests/test_auth_and_tenancy.py::test_crud_lifecycle PASSED
tests/test_auth_and_tenancy.py::test_invalid_widget_payload_is_422_not_500 PASSED
tests/test_auth_and_tenancy.py::test_tenant_b_cannot_read_or_modify_tenant_a_widgets PASSED
tests/test_auth_and_tenancy.py::test_tenant_b_cannot_see_tenant_a_submissions PASSED
```

Live check from the probe run:

```
  PASS  no auth is rejected (got 401)
```

#### ☑ Multi-tenant isolation proven: tenant A cannot read or modify tenant B's widgets or submissions

`test_tenant_b_cannot_read_or_modify_tenant_a_widgets` asserts `404` on GET,
PATCH and DELETE of another tenant's widget, and an empty list.
`test_tenant_b_cannot_see_tenant_a_submissions` asserts a second tenant sees
`total: 0` and is refused (`404`) when filtering by the first tenant's widget id.

Live, against two freshly registered accounts:

```
PROBE + — tenant isolation (a second account cannot see the first's data)
  PASS  other tenant gets 404 on our widget (got 404)
  PASS  other tenant sees no submissions (got 0)
  PASS  no auth is rejected (got 401)
```

Isolation is enforced in the SQL predicate itself (`app/services/widget_service.py`,
`app/services/dashboard_service.py`), not in a view layer. Another tenant's widget
returns `404` rather than `403`, so the API never confirms that someone else's id
is real.

#### ☑ Embed snippet generated per widget

From `docker compose exec api python scripts/seed.py`:

```
  Acme widgets:
    Contact sales          public_id=aaa257a35816e603
      <script src="http://localhost:8000/embed/widget.v1.js?id=aaa257a35816e603" async></script>
    Newsletter signup      public_id=8f851622d87a5373
      <script src="http://localhost:8000/embed/widget.v1.js?id=8f851622d87a5373" async></script>
```

Asserted by `test_embed_snippet_points_at_the_versioned_bundle`.

### Widget delivery

#### ☑ Public config endpoint serves a small payload with correct HTTP cache headers
#### ☑ Widget JavaScript is served as a versioned bundle

```
$ curl -sS -D - -o /dev/null http://localhost:8000/embed/widget.v1.js
HTTP/1.1 200 OK
cache-control: public, max-age=31536000, immutable
etag: "7cce91cd839973489e04d0cb1842a550"
access-control-allow-origin: *
x-widget-version: v1
content-type: application/javascript; charset=utf-8

$ curl -sS -D - -o /dev/null http://localhost:8000/embed/widget.v1.js -H 'If-None-Match: "7cce91cd839973489e04d0cb1842a550"'
HTTP/1.1 304 Not Modified
etag: "7cce91cd839973489e04d0cb1842a550"

$ curl -sS -D - -o /dev/null 'http://localhost:8000/api/public/widgets/8f851622d87a5373/config' -H 'Origin: http://localhost:5501'
HTTP/1.1 200 OK
cache-control: public, max-age=60
etag: "8f851622d87a5373-v1"
vary: Origin
content-length: 591
access-control-allow-origin: *
access-control-expose-headers: ETag, X-RateLimit-Limit, X-RateLimit-Remaining, Retry-After

$ curl -sS -X OPTIONS http://localhost:8000/api/public/submissions -H 'Origin: http://localhost:5501' -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: content-type,idempotency-key' -D -
HTTP/1.1 200 OK
access-control-allow-origin: *
access-control-allow-methods: GET, POST, PATCH, DELETE, OPTIONS
access-control-max-age: 600
access-control-allow-headers: Accept, Accept-Language, Authorization, Content-Language, Content-Type, Idempotency-Key, If-None-Match

$ curl -sS http://localhost:8000/api/public/widgets/8f851622d87a5373/config    # the payload the widget fetches
{
    "id": "8f851622d87a5373",
    "version": 1,
    "type": "signup_form",
    "title": "Join the Acme newsletter",
    "description": "Product news once a month. No spam, unsubscribe anytime.",
    "button_text": "Subscribe",
    "success_message": "You're on the list \u2014 check your inbox.",
    "fields": [
        {
            "name": "email",
            "label": "Email",
            "type": "email",
            "required": true,
            "placeholder": "you@company.com"
        },
        {
            "name": "first_name",
            "label": "First name",
            "type": "text",
            "required": false,
            "placeholder": null
        }
    ],
    "display": {
        "theme": "light",
        "accent": "#2563eb"
    },
    "honeypot_field": "website_url",
    "submit_url": "http://localhost:8000/api/public/submissions"
}
```

The config payload is **591 bytes**. The bundle is cached for a year and marked
`immutable` because its version is in the URL; the config is short-lived with an
ETag built from `config_version`, so a `PATCH` to the widget invalidates it at
once (`test_config_etag_changes_when_the_widget_changes`) while an unchanged
config answers `304`.

```
tests/test_delivery_and_cors.py::test_bundle_is_versioned_and_cached_forever PASSED
tests/test_delivery_and_cors.py::test_bundle_revalidates_with_etag PASSED
tests/test_delivery_and_cors.py::test_unknown_bundle_version_is_404 PASSED
tests/test_delivery_and_cors.py::test_config_is_small_public_and_short_lived PASSED
tests/test_delivery_and_cors.py::test_config_etag_changes_when_the_widget_changes PASSED
tests/test_delivery_and_cors.py::test_inactive_and_unknown_widgets_are_404 PASSED
tests/test_delivery_and_cors.py::test_preflight_is_answered_for_the_submission_endpoint PASSED
tests/test_delivery_and_cors.py::test_cross_origin_post_carries_cors_headers PASSED
tests/test_delivery_and_cors.py::test_per_widget_origin_allowlist_is_enforced PASSED
```

#### ☑ The widget renders on a page served from a different origin than your API

![The widget rendered on the second-origin customer page](docs/images/widget-on-second-origin.png)

The page is served by `python -m http.server` on `localhost:5501`; the API is on
`localhost:8000`. The widget was submitted from that page in a real browser, and
the row landed with the second origin recorded:

```
$ docker compose exec -T db psql -U leadcapture -d leadcapture \
    -c "SELECT data->>'email', origin, geo_status, country FROM submissions
        WHERE origin = 'http://localhost:5501' ORDER BY created_at DESC LIMIT 2;"

           ?column?           |         origin         | geo_status | country
------------------------------+------------------------+------------+---------
 second-visitor@northwind.example | http://localhost:5501 | enriched   | Kenya
 browser-visitor@northwind.example | http://localhost:5501 | enriched   | Kenya
```

### Public submission API

#### ☑ Cross-origin submissions work: CORS headers correct, preflight (OPTIONS) handled

The `OPTIONS` transcript above returns `200` with `access-control-allow-origin`,
`access-control-allow-methods` including `POST`, and `access-control-allow-headers`
covering `Content-Type` and `Idempotency-Key`. Asserted by
`test_preflight_is_answered_for_the_submission_endpoint` and
`test_cross_origin_post_carries_cors_headers`.

#### ☑ All incoming input validated; malformed and oversized payloads rejected with 4xx and JSON errors

See PROBE 2 above, plus:

```
tests/test_submission_validation.py::test_valid_submission_is_stored PASSED
tests/test_submission_validation.py::test_missing_required_field_is_422 PASSED
tests/test_submission_validation.py::test_bad_email_is_422 PASSED
tests/test_submission_validation.py::test_unknown_field_is_rejected PASSED
tests/test_submission_validation.py::test_malformed_json_is_4xx_not_500 PASSED
tests/test_submission_validation.py::test_missing_widget_id_is_422 PASSED
tests/test_submission_validation.py::test_unknown_widget_is_404 PASSED
tests/test_submission_validation.py::test_extra_top_level_keys_are_refused PASSED
tests/test_submission_validation.py::test_nested_structures_in_data_are_refused PASSED
tests/test_submission_validation.py::test_oversized_payload_is_413 PASSED
tests/test_submission_validation.py::test_oversized_single_field_under_body_limit_is_422 PASSED
tests/test_submission_validation.py::test_no_endpoint_returns_500_for_hostile_input PASSED
tests/test_submission_validation.py::test_idempotency_key_stores_one_row_for_a_retry PASSED
tests/test_submission_validation.py::test_malformed_json_reports_body_not_a_byte_offset PASSED
```

`test_no_endpoint_returns_500_for_hostile_input` fires SQL-injection strings,
script tags, negative numbers and a null body at the endpoint and asserts nothing
returns a 5xx.

#### ☑ Valid submissions stored safely, linked to the right widget and tenant

`test_valid_submission_is_stored` asserts the stored row's `widget_id` and
`tenant_id`. Storage uses SQLAlchemy parameter binding throughout — there is no
string-built SQL anywhere in the codebase.

### Abuse protection

#### ☑ Rate limiting per IP and/or per widget returns 429 under a burst — and the API keeps serving

PROBE 3 above: 40 rapid submissions produced 27×`201` and 13×`429`, while
`/healthz` and the config endpoint both continued answering `200`.

```
tests/test_protection.py::test_sliding_window_allows_up_to_the_limit_then_refuses PASSED
tests/test_protection.py::test_buckets_are_independent_per_key PASSED
tests/test_protection.py::test_ip_and_widget_buckets_are_checked_separately PASSED
tests/test_protection.py::test_burst_returns_429_and_the_service_keeps_serving PASSED
tests/test_protection.py::test_a_legitimate_request_from_another_ip_still_succeeds_during_a_flood PASSED
tests/test_protection.py::test_rate_limit_headers_are_exposed PASSED
tests/test_protection.py::test_filled_honeypot_is_silently_dropped PASSED
tests/test_protection.py::test_empty_honeypot_passes_through PASSED
tests/test_protection.py::test_implausibly_fast_fill_is_treated_as_spam PASSED
tests/test_protection.py::test_spam_blocked_count_reaches_the_dashboard PASSED
```

`test_a_legitimate_request_from_another_ip_still_succeeds_during_a_flood` proves
the important half: one flooder is throttled without collateral damage to
everyone else.

#### ☑ At least one spam-prevention technique demonstrably blocks a spam submission

PROBE 6 above. Two techniques ship: a honeypot field
(`test_filled_honeypot_is_silently_dropped`) and a fill-time heuristic
(`test_implausibly_fast_fill_is_treated_as_spam`).

### Enrichment & safe side effects

#### ☑ IP→geo enrichment uses a provider fallback chain: A down → B answers
#### ☑ All providers down → submission still succeeds (without geo)

PROBE 4 above, plus a deterministic unit-level matrix:

```
tests/test_enrichment.py::test_provider_a_answers_and_b_is_never_called PASSED
tests/test_enrichment.py::test_provider_a_down_falls_through_to_b PASSED
tests/test_enrichment.py::test_a_crashing_provider_does_not_break_the_chain PASSED
tests/test_enrichment.py::test_all_providers_down_returns_unavailable_and_never_raises PASSED
tests/test_enrichment.py::test_private_ip_is_skipped_when_no_dev_fallback_is_configured PASSED
tests/test_enrichment.py::test_submission_is_enriched_by_mock_provider_a PASSED
tests/test_enrichment.py::test_provider_a_down_submission_enriched_by_provider_b PASSED
tests/test_enrichment.py::test_both_providers_down_submission_still_succeeds_without_geo PASSED
tests/test_enrichment.py::test_fallback_matrix[True-True-mock-provider-a] PASSED
tests/test_enrichment.py::test_fallback_matrix[False-True-mock-provider-b] PASSED
tests/test_enrichment.py::test_fallback_matrix[False-False-None] PASSED
```

`test_a_crashing_provider_does_not_break_the_chain` covers the case a provider
raises something unplanned rather than a polite `ProviderError` — the chain
absorbs that too.

**The two real providers were also verified against the live APIs**, not just
mocked. With `GEO_MODE=live`:

```
$ curl -sS -X POST http://localhost:8000/api/public/submissions -H 'Content-Type: application/json' \
    -H 'Origin: http://localhost:5501' -d '{"widget_id":"...","data":{"email":"live-geo-test@example.com"}}'
{"status": "ok", "id": "6a1c5f40-2683-4c41-8b89-b7fecc54db3c", ...}

 geo_status | geo_provider |    country    | country_code |  city
------------+--------------+---------------+--------------+---------
 enriched   | ip-api.com   | United States | US           | Ashburn
```

Provider B was rate-limited by its own free tier at the time of testing, which
is itself a useful result — the chain degraded exactly as designed:

```
  ip-api.com     OK   -> United States / US / Ashburn
  ipapi.co       FAIL -> ipapi.co: Client error '429 Too Many Requests'
  chain[deadA -> liveB]: unavailable via None -> None
```

Because that left provider B's *success*-parsing path unexercised, it is covered
by HTTP-level tests using the real payload shapes of both APIs (mocked with
`respx` so the suite needs no network and cannot go flaky on a rate limit):

```
tests/test_live_providers.py::test_ip_api_parses_a_successful_response PASSED
tests/test_live_providers.py::test_ip_api_treats_a_200_with_status_fail_as_an_error PASSED
tests/test_live_providers.py::test_ip_api_raises_on_http_error_and_on_junk_body PASSED
tests/test_live_providers.py::test_ip_api_raises_on_timeout PASSED
tests/test_live_providers.py::test_ipapi_co_parses_a_successful_response PASSED
tests/test_live_providers.py::test_ipapi_co_treats_a_200_with_error_true_as_an_error PASSED
tests/test_live_providers.py::test_ipapi_co_raises_on_429_status PASSED
tests/test_live_providers.py::test_live_chain_falls_through_from_a_to_b PASSED
tests/test_live_providers.py::test_live_chain_degrades_when_both_real_providers_fail PASSED
```

These pin down the detail most likely to break silently in production: the two
APIs use different key names (`countryCode`/`regionName` vs
`country_code`/`region`), and both signal some failures with a `200` response
carrying an error in the body rather than an HTTP error status.

#### ☑ A failing confirmation email / webhook does not prevent the submission from being stored

PROBE 5 shows the submission returning `201` while its side effect throws. Here
is the full retry chain running to exhaustion with `SIDE_EFFECT_FORCE_FAIL=true`:

```
2026-08-29 05:41:08,959 WARNING app.outbox.worker outbox.retry event_id=d2305719-0fed-406c-86d2-7c98e912604f type=submission.email attempt=1/5 next_attempt=2026-08-29T05:41:10.959533+00:00 error=SideEffectError: SIDE_EFFECT_FORCE_FAIL=true — deliberate side-effect failure
2026-08-29 05:41:10,982 WARNING app.outbox.worker outbox.retry event_id=d2305719-0fed-406c-86d2-7c98e912604f type=submission.email attempt=2/5 next_attempt=2026-08-29T05:41:14.982848+00:00 error=SideEffectError: SIDE_EFFECT_FORCE_FAIL=true — deliberate side-effect failure
2026-08-29 05:41:15,016 WARNING app.outbox.worker outbox.retry event_id=d2305719-0fed-406c-86d2-7c98e912604f type=submission.email attempt=3/5 next_attempt=2026-08-29T05:41:23.016316+00:00 error=SideEffectError: SIDE_EFFECT_FORCE_FAIL=true — deliberate side-effect failure
2026-08-29 05:41:23,066 WARNING app.outbox.worker outbox.retry event_id=d2305719-0fed-406c-86d2-7c98e912604f type=submission.email attempt=4/5 next_attempt=2026-08-29T05:41:39.066335+00:00 error=SideEffectError: SIDE_EFFECT_FORCE_FAIL=true — deliberate side-effect failure
2026-08-29 05:41:39,156 ERROR app.outbox.worker ALERT outbox.dead_letter event_id=d2305719-0fed-406c-86d2-7c98e912604f type=submission.email attempts=5 error=SideEffectError: SIDE_EFFECT_FORCE_FAIL=true — deliberate side-effect failure
```

Backoff grows 2s → 4s → 8s → 16s, then the event is dead-lettered with an
`ALERT` log line. And the submission itself is untouched:

```
-- the submission whose side effect died is still stored, intact:
             lead             | geo_status | country |    event_type    | outbox_status | attempts 
------------------------------+------------+---------+------------------+---------------+----------
 dead-letter-demo@example.com | enriched   | Kenya   | submission.email | failed        |        5
(1 row)
```

The lead is stored and enriched; only the side effect failed.

The happy path, through Mailpit:

```
-- Mailpit inbox (the email side effect actually delivered):
  total messages: 69
  From: no-reply@leadcapture.local  To: leads@acme.example.com
  Subject: New submission on 'Newsletter signup'
  From: no-reply@leadcapture.local  To: owner@example.com
  Subject: New submission on 'Probe widget'
  From: no-reply@leadcapture.local  To: owner@example.com
  Subject: New submission on 'Probe widget'

-- outbox event delivered:
    event_type    |  status   | attempts | last_error 
------------------+-----------+----------+------------
 submission.email | delivered |        1 | 
 submission.email | delivered |        4 | 
 submission.email | delivered |        1 | 
(3 rows)
```

Note the event delivered on **attempt 4** — that event was queued while the
forced-failure flag was on, kept retrying, and succeeded once the dependency
recovered. That is the background job's retry policy working end to end, not a
staged demo.

```
tests/test_outbox_side_effects.py::test_side_effect_is_queued_not_performed_on_the_request_path PASSED
tests/test_outbox_side_effects.py::test_failing_side_effect_does_not_prevent_storage PASSED
tests/test_outbox_side_effects.py::test_no_outbox_event_when_the_widget_has_no_notifications PASSED
tests/test_outbox_side_effects.py::test_both_email_and_webhook_events_are_queued PASSED
tests/test_outbox_side_effects.py::test_worker_delivers_a_healthy_event PASSED
tests/test_outbox_side_effects.py::test_worker_retries_with_growing_backoff_before_giving_up PASSED
tests/test_outbox_side_effects.py::test_worker_ignores_events_that_are_not_due_yet PASSED
tests/test_outbox_side_effects.py::test_a_delivered_event_is_never_processed_twice PASSED
tests/test_outbox_side_effects.py::test_webhook_to_an_unreachable_host_is_retried_not_fatal PASSED
tests/test_outbox_side_effects.py::test_embed_snippet_points_at_the_versioned_bundle PASSED
```

### Documentation

#### ☑ README with architecture diagram, setup instructions, and API documentation

[README.md](README.md) — ASCII architecture diagram of all three request paths, a
screenshot of the widget on a second origin, one-command setup, the full endpoint
table, a section on how each hard part works, and an honest limitations list.

#### ☑ Required files from Section 11 present

| File | |
|---|---|
| `README.md` | architecture, setup, API docs, limitations |
| `capstone.yaml` | `run:`, `seed:`, `test:`, `base_url:`, endpoints |
| `EVIDENCE.md` | this file |
| `BUILDLOG.md` | honest AI-usage log |
| `.env.example` | every variable, placeholder values |
| `LICENSE` | MIT |
| `docs/design.md` | the Phase 1 one-page design document |

---

## Shared requirements (Section 13)

### 1 — Layered architecture: data / logic / HTTP separated

`app/routers/` does HTTP only (parse, authorise, headers, status codes).
`app/services/` holds business logic, tenant scoping and transactions.
`app/models.py` + `app/db.py` are persistence. Cross-cutting concerns are their
own testable units: `ratelimit.py`, `spam.py`, `enrichment/`, `outbox/`,
`security.py`, `errors.py`.

The split is load-bearing, not cosmetic: every enrichment and rate-limit test
above runs against those units directly, with no HTTP involved.

### 2 — Validation at the boundary: bad input → clean 4xx, never a 500

Pydantic with `extra="forbid"` rejects unknown keys before business logic runs;
per-widget field validation happens in the service; a middleware refuses
oversized bodies with `413` before any parser sees them. Three exception handlers
guarantee one JSON error shape. Proven by PROBE 2 and
`test_no_endpoint_returns_500_for_hostile_input`.

### 3 — ≥1 background job: slow work off the request path, retries + failure alert

The outbox worker. The request path never sends an email or calls a webhook — it
writes an `outbox_events` row in the same transaction as the submission. See the
dead-letter transcript above: 5 attempts, exponential backoff, then an `ALERT`
log line.

### 4 — Real persistence: schema as migrations, right indexes, isolated tenants

```
-- migrations applied (schema is versioned, not hand-built):
0001 (head)

-- tables:
               List of relations
 Schema |      Name       | Type  |    Owner    
--------+-----------------+-------+-------------
 public | alembic_version | table | leadcapture
 public | outbox_events   | table | leadcapture
 public | spam_events     | table | leadcapture
 public | submissions     | table | leadcapture
 public | tenants         | table | leadcapture
 public | widgets         | table | leadcapture
(6 rows)

-- indexes and constraints:
alembic_version  ->  alembic_version_pkc
outbox_events  ->  ix_outbox_status_next_attempt
outbox_events  ->  outbox_events_pkey
spam_events  ->  ix_spam_events_tenant_created
spam_events  ->  spam_events_pkey
submissions  ->  ix_submissions_tenant_id_created_at
submissions  ->  ix_submissions_widget_id_created_at
submissions  ->  submissions_pkey
submissions  ->  uq_submissions_widget_idem
tenants  ->  ix_tenants_email
tenants  ->  tenants_pkey
widgets  ->  ix_widgets_public_id
widgets  ->  ix_widgets_tenant_id_created_at
widgets  ->  widgets_pkey
```

`uq_submissions_widget_idem` backs idempotency at the database level;
`ix_outbox_status_next_attempt` is what makes the worker's claim query cheap;
the two `(tenant_id, created_at)` indexes serve every dashboard query.

### 5 — Idempotency where it matters: the retried action happens once

Two layers, both tested:

- A client's `Idempotency-Key` on a submission — `test_idempotency_key_stores_one_row_for_a_retry`
  posts twice and asserts one row and the same returned id. A concurrent race
  loses at the unique constraint, not in application logic.
- `SELECT ... FOR UPDATE SKIP LOCKED` in the outbox worker — one event is claimed
  by exactly one worker, so the confirmation email is sent once
  (`test_a_delivered_event_is_never_processed_twice`).

### 6 — Secrets clean: env only, encrypted if stored, never logged

Every setting comes from the environment via `app/config.py`. `.env` is in
`.gitignore` from the first commit; `.env.example` ships placeholders only.
Passwords are bcrypt-hashed (`app/security.py`) and never logged. No credential
appears anywhere in the git history.

### 7 — Cost tracked, if AI is used — per call, attributed, with a budget guard

**Not applicable.** This system makes no AI/LLM calls at runtime. AI was used to
help *write* the code, which is logged honestly in [BUILDLOG.md](BUILDLOG.md).

---

## Full test suite

```
tests/test_auth_and_tenancy.py::test_register_then_login_issues_a_token PASSED
tests/test_auth_and_tenancy.py::test_duplicate_email_is_rejected PASSED
tests/test_auth_and_tenancy.py::test_wrong_password_is_401_with_a_generic_message PASSED
tests/test_auth_and_tenancy.py::test_widget_routes_reject_missing_and_invalid_auth PASSED
tests/test_auth_and_tenancy.py::test_crud_lifecycle PASSED
tests/test_auth_and_tenancy.py::test_invalid_widget_payload_is_422_not_500 PASSED
tests/test_auth_and_tenancy.py::test_tenant_b_cannot_read_or_modify_tenant_a_widgets PASSED
tests/test_auth_and_tenancy.py::test_tenant_b_cannot_see_tenant_a_submissions PASSED
tests/test_dashboard.py::test_submission_from_a_second_origin_is_visible_to_its_owner PASSED
tests/test_dashboard.py::test_submissions_are_paginated_and_newest_first PASSED
tests/test_dashboard.py::test_summary_aggregates_across_widgets PASSED
tests/test_dashboard.py::test_enrichment_success_rate_reflects_a_dead_provider_chain PASSED
tests/test_dashboard.py::test_widget_stats_bucket_by_day_and_country PASSED
tests/test_dashboard.py::test_empty_dashboard_does_not_divide_by_zero PASSED
tests/test_dashboard.py::test_health_endpoints PASSED
tests/test_delivery_and_cors.py::test_bundle_is_versioned_and_cached_forever PASSED
tests/test_delivery_and_cors.py::test_bundle_revalidates_with_etag PASSED
tests/test_delivery_and_cors.py::test_unknown_bundle_version_is_404 PASSED
tests/test_delivery_and_cors.py::test_config_is_small_public_and_short_lived PASSED
tests/test_delivery_and_cors.py::test_config_etag_changes_when_the_widget_changes PASSED
tests/test_delivery_and_cors.py::test_inactive_and_unknown_widgets_are_404 PASSED
tests/test_delivery_and_cors.py::test_preflight_is_answered_for_the_submission_endpoint PASSED
tests/test_delivery_and_cors.py::test_cross_origin_post_carries_cors_headers PASSED
tests/test_delivery_and_cors.py::test_per_widget_origin_allowlist_is_enforced PASSED
tests/test_delivery_and_cors.py::test_head_works_on_cache_facing_assets PASSED
tests/test_enrichment.py::test_provider_a_answers_and_b_is_never_called PASSED
tests/test_enrichment.py::test_provider_a_down_falls_through_to_b PASSED
tests/test_enrichment.py::test_a_crashing_provider_does_not_break_the_chain PASSED
tests/test_enrichment.py::test_all_providers_down_returns_unavailable_and_never_raises PASSED
tests/test_enrichment.py::test_private_ip_is_skipped_when_no_dev_fallback_is_configured PASSED
tests/test_enrichment.py::test_submission_is_enriched_by_mock_provider_a PASSED
tests/test_enrichment.py::test_provider_a_down_submission_enriched_by_provider_b PASSED
tests/test_enrichment.py::test_both_providers_down_submission_still_succeeds_without_geo PASSED
tests/test_enrichment.py::test_fallback_matrix[True-True-mock-provider-a] PASSED
tests/test_enrichment.py::test_fallback_matrix[False-True-mock-provider-b] PASSED
tests/test_enrichment.py::test_fallback_matrix[False-False-None] PASSED
tests/test_live_providers.py::test_ip_api_parses_a_successful_response PASSED
tests/test_live_providers.py::test_ip_api_treats_a_200_with_status_fail_as_an_error PASSED
tests/test_live_providers.py::test_ip_api_raises_on_http_error_and_on_junk_body PASSED
tests/test_live_providers.py::test_ip_api_raises_on_timeout PASSED
tests/test_live_providers.py::test_ipapi_co_parses_a_successful_response PASSED
tests/test_live_providers.py::test_ipapi_co_treats_a_200_with_error_true_as_an_error PASSED
tests/test_live_providers.py::test_ipapi_co_raises_on_429_status PASSED
tests/test_live_providers.py::test_live_chain_falls_through_from_a_to_b PASSED
tests/test_live_providers.py::test_live_chain_degrades_when_both_real_providers_fail PASSED
tests/test_outbox_side_effects.py::test_side_effect_is_queued_not_performed_on_the_request_path PASSED
tests/test_outbox_side_effects.py::test_failing_side_effect_does_not_prevent_storage PASSED
tests/test_outbox_side_effects.py::test_no_outbox_event_when_the_widget_has_no_notifications PASSED
tests/test_outbox_side_effects.py::test_both_email_and_webhook_events_are_queued PASSED
tests/test_outbox_side_effects.py::test_worker_delivers_a_healthy_event PASSED
tests/test_outbox_side_effects.py::test_worker_retries_with_growing_backoff_before_giving_up PASSED
tests/test_outbox_side_effects.py::test_worker_ignores_events_that_are_not_due_yet PASSED
tests/test_outbox_side_effects.py::test_a_delivered_event_is_never_processed_twice PASSED
tests/test_outbox_side_effects.py::test_webhook_to_an_unreachable_host_is_retried_not_fatal PASSED
tests/test_outbox_side_effects.py::test_embed_snippet_points_at_the_versioned_bundle PASSED
tests/test_protection.py::test_sliding_window_allows_up_to_the_limit_then_refuses PASSED
tests/test_protection.py::test_buckets_are_independent_per_key PASSED
tests/test_protection.py::test_ip_and_widget_buckets_are_checked_separately PASSED
tests/test_protection.py::test_burst_returns_429_and_the_service_keeps_serving PASSED
tests/test_protection.py::test_a_legitimate_request_from_another_ip_still_succeeds_during_a_flood PASSED
tests/test_protection.py::test_rate_limit_headers_are_exposed PASSED
tests/test_protection.py::test_filled_honeypot_is_silently_dropped PASSED
tests/test_protection.py::test_empty_honeypot_passes_through PASSED
tests/test_protection.py::test_implausibly_fast_fill_is_treated_as_spam PASSED
tests/test_protection.py::test_spam_blocked_count_reaches_the_dashboard PASSED
tests/test_submission_validation.py::test_valid_submission_is_stored PASSED
tests/test_submission_validation.py::test_missing_required_field_is_422 PASSED
tests/test_submission_validation.py::test_bad_email_is_422 PASSED
tests/test_submission_validation.py::test_unknown_field_is_rejected PASSED
tests/test_submission_validation.py::test_malformed_json_is_4xx_not_500 PASSED
tests/test_submission_validation.py::test_missing_widget_id_is_422 PASSED
tests/test_submission_validation.py::test_unknown_widget_is_404 PASSED
tests/test_submission_validation.py::test_extra_top_level_keys_are_refused PASSED
tests/test_submission_validation.py::test_nested_structures_in_data_are_refused PASSED
tests/test_submission_validation.py::test_oversized_payload_is_413 PASSED
tests/test_submission_validation.py::test_oversized_single_field_under_body_limit_is_422 PASSED
tests/test_submission_validation.py::test_no_endpoint_returns_500_for_hostile_input PASSED
tests/test_submission_validation.py::test_idempotency_key_stores_one_row_for_a_retry PASSED
tests/test_submission_validation.py::test_malformed_json_reports_body_not_a_byte_offset PASSED

79 passed
```
