#!/bin/bash
# 매일 자동 실행되는 예측 업데이트 스크립트
# launchd / cron 에서 호출됨

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/update_$(date +%Y-%m-%d).log"

{
  echo "================================================"
  echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] 예측 업데이트 시작"
  echo "================================================"

  "$ROOT/.venv/bin/python" "$ROOT/scripts/predict.py"

  echo ""
  echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] 완료"
  echo "  predictions.json: $ROOT/web/data/predictions.json"
  echo "  스냅샷: $ROOT/data/processed/history/$(date +%Y-%m-%d).json"
} 2>&1 | tee -a "$LOG_FILE"

# 로그는 30일치만 보관
find "$LOG_DIR" -name "update_*.log" -mtime +30 -delete 2>/dev/null || true
