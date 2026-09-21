#!/usr/bin/env bash
#
# Checks the four host ports this stack binds before Docker tries to.
#
# Without this, a conflict on any single port aborts the whole `docker compose
# up` — including the API — and Docker's only explanation is a wall of text
# ending in "address already in use". This turns that into one actionable line.
#
#   ./scripts/preflight.sh

set -uo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] && { set -a; . ./.env; set +a; }

declare -A PORTS=(
  ["API_PORT"]="${API_PORT:-8000}"
  ["TESTSITE_PORT"]="${TESTSITE_PORT:-5500}"
  ["POSTGRES_PORT"]="${POSTGRES_PORT:-5433}"
  ["MAILPIT_UI_PORT"]="${MAILPIT_UI_PORT:-8025}"
)

in_use() {
  # Prefer ss, fall back to bash's /dev/tcp probe.
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | grep -qE "[:.]$1[[:space:]]"
  else
    (echo > "/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1
  fi
}

holder() {
  command -v ss >/dev/null 2>&1 || return 0
  ss -ltnp 2>/dev/null | grep -E "[:.]$1[[:space:]]" \
    | grep -oE 'users:\(\("[^"]+"' | head -1 | sed 's/users:((\"//;s/\"//'
}

CONFLICTS=0
echo "Checking host ports..."
for var in API_PORT TESTSITE_PORT POSTGRES_PORT MAILPIT_UI_PORT; do
  port="${PORTS[$var]}"
  if in_use "$port"; then
    who="$(holder "$port")"
    printf '  BUSY  %-16s %s%s\n' "$var" "$port" "${who:+   (held by: $who)}"
    CONFLICTS=$((CONFLICTS + 1))
  else
    printf '  free  %-16s %s\n' "$var" "$port"
  fi
done

if [ "$CONFLICTS" -eq 0 ]; then
  echo
  echo "All clear — run: docker compose up -d"
  exit 0
fi

echo
echo "$CONFLICTS port(s) already in use. Docker would abort the WHOLE stack over this,"
echo "so set a free port in .env and re-run. For example:"
echo
for var in API_PORT TESTSITE_PORT POSTGRES_PORT MAILPIT_UI_PORT; do
  port="${PORTS[$var]}"
  if in_use "$port"; then
    candidate=$((port + 1))
    while in_use "$candidate"; do candidate=$((candidate + 1)); done
    echo "  sed -i 's/^${var}=.*/${var}=${candidate}/' .env"
  fi
done
echo
echo "Then: docker compose up -d"
exit 1
