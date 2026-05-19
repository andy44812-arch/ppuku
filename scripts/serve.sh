#!/bin/bash
# 로컬 웹서버 실행 — http://localhost:8765 에서 대시보드 보기
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8765}"

echo "📊 부산 북구갑 보궐선거 예측 대시보드"
echo "   ➜  http://localhost:$PORT"
echo "   (종료: Ctrl+C)"
echo ""

# 브라우저 자동 오픈 (macOS)
if command -v open >/dev/null 2>&1; then
  (sleep 1 && open "http://localhost:$PORT") &
fi

cd "$ROOT/web"
exec "$ROOT/.venv/bin/python" -m http.server "$PORT" --bind 127.0.0.1
