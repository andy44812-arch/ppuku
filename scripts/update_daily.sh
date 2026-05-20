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
  echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] 예측 업데이트 시작 (부산 + 서울)"
  echo "================================================"

  echo ""
  echo "--- [1/2] 부산 북구갑 보궐선거 ---"
  "$ROOT/.venv/bin/python" "$ROOT/scripts/predict_busan.py"

  echo ""
  echo "--- [2/2] 서울특별시장 선거 ---"
  "$ROOT/.venv/bin/python" "$ROOT/scripts/predict_seoul.py"

  echo ""
  echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] 완료"
  echo "  부산 predictions: $ROOT/web/data/busan/predictions.json"
  echo "  서울 predictions: $ROOT/web/data/seoul/predictions.json"
  echo "  부산 스냅샷: $ROOT/data/processed/busan/history/$(date +%Y-%m-%d).json"
  echo "  서울 스냅샷: $ROOT/data/processed/seoul/history/$(date +%Y-%m-%d).json"
} 2>&1 | tee -a "$LOG_FILE"

# 로그는 30일치만 보관
find "$LOG_DIR" -name "update_*.log" -mtime +30 -delete 2>/dev/null || true
