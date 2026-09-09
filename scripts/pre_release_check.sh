#!/usr/bin/env bash
# 发版门禁(2026-09-09, 第6项): tsc + ruff + CI同款pytest + marketdata + 联网集尝试。
# 用法: bash scripts/pre_release_check.sh [--with-agent-smoke]
#   前4步任一失败即退出(阻断发版); 第5步联网集只记录状态不阻断
#   (海外机房直连国内行情源本就不通, 状态供发版人判断)。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

export AUTH_ALLOW_DEFAULT_ADMIN="${AUTH_ALLOW_DEFAULT_ADMIN:-1}"
export AUTH_PASSWORD="${AUTH_PASSWORD:-xz.170530}"
PY="${PY:-.venv/bin/python}"

WITH_AGENT_SMOKE=0
for arg in "$@"; do
  case "$arg" in
    --with-agent-smoke) WITH_AGENT_SMOKE=1 ;;
    *) echo "Unknown arg: $arg"; echo "Usage: $0 [--with-agent-smoke]"; exit 2 ;;
  esac
done

run_step() {
  local title="$1"
  shift
  echo
  echo "==> $title"
  "$@"
}

CI_IGNORES=(
  --ignore=tests/test_dark_flow.py
  --ignore=tests/test_dark_l2_engine.py
  --ignore=tests/test_thsdk_api.py
  --ignore=tests/test_thsdk_board.py
  --ignore=tests/test_thsdk_extended.py
  --ignore=tests/test_thsdk_ext.py
  --ignore=tests/test_main_flow_compare.py
  --ignore=tests/test_chat_thsdk_tools.py
  --ignore=tests/test_auction_pool.py
  --ignore=tests/test_events_routing.py
  --ignore=tests/test_context_enrichments.py
  --ignore=tests/test_datasource_admin_api.py
  --ignore=tests/test_datasource_reconcile.py
  --ignore=tests/test_datasources_health.py
  --ignore=tests/test_selfcheck.py
  --ignore=tests/test_shadow_account.py
  --ignore=tests/test_ta_us_news_passthrough.py
)

run_step "1/5 Frontend typecheck (tsc -b)" \
  ./frontend/node_modules/.bin/tsc -b --pretty false

run_step "2/5 Ruff (E9,F821,F601,F811)" \
  python3 -m ruff check src/ server.py forecast_server.py scripts/ packages/marketdata/src \
    --select E9,F821,F601,F811

run_step "3/5 Backend tests (CI同款排除表)" \
  "$PY" -m pytest tests/ -q --tb=short -p no:warnings "${CI_IGNORES[@]}"

run_step "4/5 marketdata package tests" \
  "$PY" -m pytest packages/marketdata/tests -q --tb=short -p no:warnings

echo
echo "==> 5/5 联网集尝试(非阻断, 仅记录)"
set +e
"$PY" -m pytest tests/test_dark_flow.py tests/test_dark_l2_engine.py \
  tests/test_thsdk_api.py tests/test_thsdk_board.py tests/test_thsdk_extended.py \
  tests/test_thsdk_ext.py tests/test_main_flow_compare.py \
  tests/test_chat_thsdk_tools.py tests/test_auction_pool.py tests/test_events_routing.py \
  tests/test_context_enrichments.py tests/test_datasource_admin_api.py \
  tests/test_datasource_reconcile.py tests/test_datasources_health.py tests/test_selfcheck.py \
  tests/test_shadow_account.py tests/test_ta_us_news_passthrough.py \
  -q --tb=no -p no:warnings 2>&1 | tail -3
echo "(联网集在海外机房失败属预期内: 直连国内行情源不通; 以国内小主机实测为准)"
set -e

if [[ "$WITH_AGENT_SMOKE" -eq 1 ]]; then
  BASE_URL="${BASE_URL:-http://localhost:8000}"
  echo
  echo "==> Agent smoke checks via API (${BASE_URL})"
  curl -fsS "${BASE_URL}/api/agents/health" >/dev/null
  for agent in daily_report premarket_outlook news_digest chart_analyst intraday_monitor; do
    echo "Trigger agent: ${agent}"
    curl -fsS -X POST "${BASE_URL}/api/agents/${agent}/trigger" >/dev/null
  done
fi

echo
echo "All pre-release gates passed (1-4阻断项全绿)."
