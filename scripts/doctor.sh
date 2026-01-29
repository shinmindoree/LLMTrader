#!/usr/bin/env bash
#
# LLMTrader "doctor" - quick local diagnosis for Docker Compose stack.
#
# Safe: read-only (no prune/down). Prints what looks broken and suggests next commands.

set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT" || exit 1

COMPOSE_CMD=(docker compose)
PROFILE_ARGS=(--profile full)

API_URL="${API_URL:-http://localhost:${API_PORT_HOST:-8000}}"
WEB_URL="${WEB_URL:-http://localhost:${WEB_PORT_HOST:-3000}}"
ADMIN_TOKEN="${ADMIN_TOKEN:-dev-admin-token}"

fail=0

hr() { printf '\n%s\n' "--------------------------------------------------------------------------------"; }
title() { hr; printf '%s\n' "$1"; }
cmd() { printf '\n$ %s\n' "$*"; }

run_allow_fail() {
  cmd "$*"
  if ! "$@"; then
    echo "(!) command failed (non-fatal)"
    fail=1
    return 0
  fi
}

run_compose_allow_fail() {
  # Prefer profile full (matches our compose usage), but fall back to plain compose.
  if "${COMPOSE_CMD[@]}" "${PROFILE_ARGS[@]}" ps >/dev/null 2>&1; then
    run_allow_fail "${COMPOSE_CMD[@]}" "${PROFILE_ARGS[@]}" "$@"
  else
    run_allow_fail "${COMPOSE_CMD[@]}" "$@"
  fi
}

title "LLMTrader Doctor"
echo "Project: $PROJECT_ROOT"
echo "Time: $(date)"

title "Disk / System"
run_allow_fail df -h /
run_allow_fail uname -a

title "Docker Engine"
if ! command -v docker >/dev/null 2>&1; then
  echo "(!) docker not found. Install Docker Desktop first."
  exit 1
fi

run_allow_fail docker version
run_allow_fail docker info

title "Compose Status"
run_compose_allow_fail ps

title "Ports (host)"
for port in 3000 8000 5432; do
  run_allow_fail bash -lc "lsof -nP -iTCP:${port} -sTCP:LISTEN | sed -n '1,5p' || true"
done

title "API Health"
run_allow_fail curl -sS "${API_URL}/api/health" || fail=1
echo

title "API Jobs (last 5)"
run_allow_fail curl -sS -H "x-admin-token: ${ADMIN_TOKEN}" "${API_URL}/api/jobs" | tail -n 5 || true

title "Web (basic)"
run_allow_fail curl -sS -I "${WEB_URL}" | sed -n '1,5p' || true

title "Recent Logs"
echo "api (last 80 lines)"
run_compose_allow_fail logs --tail 80 api || true
echo
echo "runner (last 80 lines)"
run_compose_allow_fail logs --tail 80 runner || true

title "If Things Look Broken (safe suggestions)"
cat <<'EOF'
1) Stop everything (DB 초기화 OK일 때):
   docker compose --profile full down --remove-orphans --volumes

2) Remove build cache (깨진 캐시 방지):
   docker builder prune -af

3) Rebuild and start:
   docker compose --profile full build --no-cache
   docker compose --profile full up -d
EOF

hr
if [[ "$fail" -eq 0 ]]; then
  echo "Doctor result: OK (no obvious failures)"
else
  echo "Doctor result: WARNING (some checks failed; scroll up)"
fi

