#!/usr/bin/env bash
# 实时跟踪后端 + 前端日志（合并、带前缀着色）
# Ctrl+C 退出 tail，不会停服务。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
cd "$ROOT_DIR"

[[ -d "$LOG_DIR" ]] || die "logs/ 不存在；先运行 make up"

# 选要看的：默认全看；可传 backend / frontend 单看
target="${1:-all}"

case "$target" in
  backend|be)
    [[ -f "$BACKEND_LOG" ]] || die "$BACKEND_LOG 不存在（后端未启动？）"
    exec tail -n 100 -F "$BACKEND_LOG"
    ;;
  frontend|fe)
    [[ -f "$FRONTEND_LOG" ]] || die "$FRONTEND_LOG 不存在（前端未启动？）"
    exec tail -n 100 -F "$FRONTEND_LOG"
    ;;
  docker|db)
    exec docker compose -f docker-compose.dev.yml logs -f --tail=100
    ;;
  all|*)
    [[ -f "$BACKEND_LOG"  ]] || touch "$BACKEND_LOG"
    [[ -f "$FRONTEND_LOG" ]] || touch "$FRONTEND_LOG"
    # awk 给每行加前缀着色，便于区分两条流
    {
      tail -n 50 -F "$BACKEND_LOG"  | awk -v c="$C_BLU" -v r="$C_RST" '{print c"[be]"r" " $0; fflush()}' &
      BE_TAIL=$!
      tail -n 50 -F "$FRONTEND_LOG" | awk -v c="$C_GRN" -v r="$C_RST" '{print c"[fe]"r" " $0; fflush()}' &
      FE_TAIL=$!
      trap 'kill $BE_TAIL $FE_TAIL 2>/dev/null || true' EXIT INT TERM
      wait
    }
    ;;
esac
