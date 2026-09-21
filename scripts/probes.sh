#!/usr/bin/env bash
#
# Runs the six acceptance probes from Section 13 of the capstone brief against
# a running stack, and prints a transcript.
#
#   docker compose up -d
#   ./scripts/probes.sh
#
# Probes 4 and 5 flip a documented environment flag and recreate the API
# container, because that is literally what "disable provider A" means here.
# The script restores .env to its original state on exit.

set -uo pipefail
cd "$(dirname "$0")/.."

API="http://localhost:${API_PORT:-8000}"
SITE_ORIGIN="http://localhost:${TESTSITE_PORT:-5500}"
ENV_BACKUP="$(mktemp)"
cp .env "$ENV_BACKUP"

PASS=0
FAIL=0

cleanup() {
  cp "$ENV_BACKUP" .env
  rm -f "$ENV_BACKUP"
  docker compose up -d api >/dev/null 2>&1
  wait_for_api
}
trap cleanup EXIT

hr()   { printf '\n%s\n' "------------------------------------------------------------------"; }
head1() { hr; printf 'PROBE %s — %s\n' "$1" "$2"; hr; }
ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
check() { if [ "$2" = "$3" ]; then ok "$1 (got $2)"; else bad "$1 (expected $3, got $2)"; fi; }

set_env() {
  # set_env KEY VALUE — rewrite one key in .env and recreate the API container.
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
  docker compose up -d api >/dev/null 2>&1
  wait_for_api
}

wait_for_api() {
  for _ in $(seq 1 40); do
    if curl -fsS "$API/healthz" >/dev/null 2>&1; then return 0; fi
    sleep 0.5
  done
  echo "API did not come back up at $API" >&2
  return 1
}

status_of() { printf '%s' "$1" | tail -n1; }
body_of()   { printf '%s' "$1" | sed '$d'; }

req() {
  # req METHOD PATH [json] [extra curl args...] -> prints body then status line
  local method="$1" path="$2" json="${3:-}"
  shift 3 2>/dev/null || shift 2
  if [ -n "$json" ]; then
    curl -sS -X "$method" "$API$path" \
      -H 'Content-Type: application/json' -H "Origin: $SITE_ORIGIN" \
      -d "$json" -w '\n%{http_code}' "$@"
  else
    curl -sS -X "$method" "$API$path" -H "Origin: $SITE_ORIGIN" -w '\n%{http_code}' "$@"
  fi
}

json_get() { python3 -c "import json,sys;d=json.load(sys.stdin);print(d$1)" 2>/dev/null; }

# ---------------------------------------------------------------- setup
wait_for_api || exit 1

EMAIL="probe-$(date +%s)@example.com"
TOKEN=$(curl -sS -X POST "$API/api/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"name\":\"Probe Co\",\"password\":\"probe-password-123\"}" \
  | json_get "['access_token']")

if [ -z "${TOKEN:-}" ]; then echo "could not register a probe tenant" >&2; exit 1; fi

WIDGET_JSON=$(curl -sS -X POST "$API/api/widgets" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Probe widget","title":"Join the list",
       "notify_email":"owner@example.com",
       "fields":[{"name":"email","label":"Email","type":"email","required":true},
                 {"name":"first_name","label":"First name","type":"text"}]}')
WID=$(printf '%s' "$WIDGET_JSON" | json_get "['public_id']")
WUUID=$(printf '%s' "$WIDGET_JSON" | json_get "['id']")

echo "Probe tenant : $EMAIL"
echo "Probe widget : $WID"
echo "Page origin  : $SITE_ORIGIN   API origin: $API"

# --------------------------------------------------------------- PROBE 1
head1 1 "cross-origin submission is stored, 2xx, and visible on the dashboard"
R=$(req POST /api/public/submissions \
  "{\"widget_id\":\"$WID\",\"data\":{\"email\":\"probe1@example.com\",\"first_name\":\"Pat\"}}" \
  -i)
echo "$R" | grep -iE '^(HTTP/|access-control-allow-origin|content-type):' | sed 's/^/  /'
CODE=$(printf '%s' "$R" | tail -n1)
check "submission accepted" "$CODE" "201"

DASH=$(curl -sS "$API/api/dashboard/submissions" -H "Authorization: Bearer $TOKEN")
TOTAL=$(printf '%s' "$DASH" | json_get "['total']")
FIRST=$(printf '%s' "$DASH" | json_get "['items'][0]['data']['email']")
GEO=$(printf '%s' "$DASH" | json_get "['items'][0]['country']")
check "visible on dashboard" "$TOTAL" "1"
check "correct row returned" "$FIRST" "probe1@example.com"
echo "  enriched country: $GEO"

# --------------------------------------------------------------- PROBE 2
head1 2 "malformed and oversized payloads get clean 4xx JSON, never a 500"

echo "-- malformed JSON:"
R=$(curl -sS -X POST "$API/api/public/submissions" -H 'Content-Type: application/json' \
  -H "Origin: $SITE_ORIGIN" -d '{"widget_id": "abc", "data": {' -w '\n%{http_code}')
printf '%s\n' "$(body_of "$R")" | sed 's/^/  /'
check "malformed JSON rejected" "$(status_of "$R")" "422"

echo "-- missing required field:"
R=$(req POST /api/public/submissions "{\"widget_id\":\"$WID\",\"data\":{\"first_name\":\"Nope\"}}")
printf '%s\n' "$(body_of "$R")" | sed 's/^/  /'
check "missing required field rejected" "$(status_of "$R")" "422"

echo "-- unknown field:"
R=$(req POST /api/public/submissions \
  "{\"widget_id\":\"$WID\",\"data\":{\"email\":\"a@b.co\",\"is_admin\":\"true\"}}")
printf '%s\n' "$(body_of "$R")" | sed 's/^/  /'
check "unknown field rejected" "$(status_of "$R")" "422"

echo "-- oversized payload:"
BIG=$(python3 -c "import json;print(json.dumps({'widget_id':'$WID','data':{'email':'a@b.co','first_name':'x'*20000}}))")
R=$(curl -sS -X POST "$API/api/public/submissions" -H 'Content-Type: application/json' \
  -H "Origin: $SITE_ORIGIN" -d "$BIG" -w '\n%{http_code}')
printf '%s\n' "$(body_of "$R")" | sed 's/^/  /'
check "oversized payload rejected" "$(status_of "$R")" "413"

# --------------------------------------------------------------- PROBE 3
head1 3 "a burst produces 429s, and a normal request right after still succeeds"
CODES=""
for _ in $(seq 1 40); do
  C=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$API/api/public/submissions" \
    -H 'Content-Type: application/json' -H "Origin: $SITE_ORIGIN" \
    -d "{\"widget_id\":\"$WID\",\"data\":{\"email\":\"flood@example.com\"}}")
  CODES="$CODES $C"
done
N201=$(printf '%s' "$CODES" | tr ' ' '\n' | grep -c '^201$')
N429=$(printf '%s' "$CODES" | tr ' ' '\n' | grep -c '^429$')
echo "  40 rapid submissions -> 201 x$N201, 429 x$N429"
if [ "$N429" -gt 0 ]; then ok "429s appeared under burst"; else bad "no 429 under burst"; fi

R=$(curl -sS "$API/healthz" -w '\n%{http_code}')
check "API still serving other traffic" "$(status_of "$R")" "200"
R=$(curl -sS "$API/api/public/widgets/$WID/config" -w '\n%{http_code}')
check "config endpoint unaffected" "$(status_of "$R")" "200"

# --------------------------------------------------------------- PROBE 4
head1 4 "geo fallback: A down -> B answers; both down -> stored without geo"

echo "-- both providers up:"
set_env GEO_MOCK_A_UP true
req POST /api/public/submissions \
  "{\"widget_id\":\"$WID\",\"data\":{\"email\":\"geo-a@example.com\"}}" >/dev/null
G=$(curl -sS "$API/api/dashboard/submissions?limit=1" -H "Authorization: Bearer $TOKEN")
P=$(printf '%s' "$G" | json_get "['items'][0]['geo_provider']")
check "provider A enriched" "$P" "mock-provider-a"

echo "-- provider A disabled:"
set_env GEO_MOCK_A_UP false
req POST /api/public/submissions \
  "{\"widget_id\":\"$WID\",\"data\":{\"email\":\"geo-b@example.com\"}}" >/dev/null
G=$(curl -sS "$API/api/dashboard/submissions?limit=1" -H "Authorization: Bearer $TOKEN")
P=$(printf '%s' "$G" | json_get "['items'][0]['geo_provider']")
C=$(printf '%s' "$G" | json_get "['items'][0]['country']")
check "provider B took over" "$P" "mock-provider-b"
echo "  country now: $C"

echo "-- both providers disabled:"
set_env GEO_MOCK_B_UP false
R=$(req POST /api/public/submissions \
  "{\"widget_id\":\"$WID\",\"data\":{\"email\":\"geo-none@example.com\"}}")
check "submission still succeeds" "$(status_of "$R")" "201"
G=$(curl -sS "$API/api/dashboard/submissions?limit=1" -H "Authorization: Bearer $TOKEN")
S=$(printf '%s' "$G" | json_get "['items'][0]['geo_status']")
E=$(printf '%s' "$G" | json_get "['items'][0]['data']['email']")
check "stored without geo" "$S" "unavailable"
check "the lead itself survived" "$E" "geo-none@example.com"

set_env GEO_MOCK_A_UP true
set_env GEO_MOCK_B_UP true

# --------------------------------------------------------------- PROBE 5
head1 5 "a throwing side effect still returns success and stores the row"
set_env SIDE_EFFECT_FORCE_FAIL true

R=$(req POST /api/public/submissions \
  "{\"widget_id\":\"$WID\",\"data\":{\"email\":\"side-effect@example.com\"}}")
printf '%s\n' "$(body_of "$R")" | sed 's/^/  /'
check "submission returned success" "$(status_of "$R")" "201"

G=$(curl -sS "$API/api/dashboard/submissions?limit=1" -H "Authorization: Bearer $TOKEN")
E=$(printf '%s' "$G" | json_get "['items'][0]['data']['email']")
check "row is stored" "$E" "side-effect@example.com"

echo "  worker log (side effect failing and retrying in the background):"
sleep 4
docker compose logs api --tail 40 2>/dev/null \
  | grep -E 'outbox\.(retry|dead_letter)' | tail -3 | sed 's/^/    /'

set_env SIDE_EFFECT_FORCE_FAIL false

# --------------------------------------------------------------- PROBE 6
head1 6 "a filled honeypot is silently dropped"
BEFORE=$(curl -sS "$API/api/dashboard/summary" -H "Authorization: Bearer $TOKEN" | json_get "['total_submissions']")

R=$(req POST /api/public/submissions \
  "{\"widget_id\":\"$WID\",\"data\":{\"email\":\"bot@spam.example\"},\"honeypot\":\"http://buy-now.example\"}")
printf '%s\n' "$(body_of "$R")" | sed 's/^/  /'
check "bot sees an ordinary success" "$(status_of "$R")" "201"

SUM=$(curl -sS "$API/api/dashboard/summary" -H "Authorization: Bearer $TOKEN")
AFTER=$(printf '%s' "$SUM" | json_get "['total_submissions']")
SPAM=$(printf '%s' "$SUM" | json_get "['spam_blocked']")
check "nothing was stored" "$AFTER" "$BEFORE"
if [ "${SPAM:-0}" -ge 1 ]; then ok "counted as spam_blocked ($SPAM)"; else bad "not counted as spam"; fi

# --------------------------------------------------------- bonus: isolation
head1 "+" "tenant isolation (a second account cannot see the first's data)"
OTHER=$(curl -sS -X POST "$API/api/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"other-$(date +%s)@example.com\",\"name\":\"Other\",\"password\":\"other-password-123\"}" \
  | json_get "['access_token']")
R=$(curl -sS "$API/api/widgets/$WUUID" -H "Authorization: Bearer $OTHER" -w '\n%{http_code}')
check "other tenant gets 404 on our widget" "$(status_of "$R")" "404"
R=$(curl -sS "$API/api/dashboard/submissions" -H "Authorization: Bearer $OTHER")
check "other tenant sees no submissions" "$(printf '%s' "$R" | json_get "['total']")" "0"
R=$(curl -sS "$API/api/widgets" -w '\n%{http_code}')
check "no auth is rejected" "$(status_of "$R")" "401"

hr
printf 'RESULT: %s passed, %s failed\n' "$PASS" "$FAIL"
hr
[ "$FAIL" -eq 0 ]
