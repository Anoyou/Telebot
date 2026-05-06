#!/usr/bin/env bash
# 通用日志/工具函数：scripts/* 共享。
# 用 source 引入：source "$(dirname "$0")/_lib.sh"

set -o pipefail

# 颜色（仅在 TTY 启用，避免污染日志）
if [[ -t 1 ]]; then
  C_RED=$'\033[0;31m'; C_GRN=$'\033[0;32m'; C_YEL=$'\033[0;33m'
  C_BLU=$'\033[0;34m'; C_DIM=$'\033[2m';   C_RST=$'\033[0m'
else
  C_RED=''; C_GRN=''; C_YEL=''; C_BLU=''; C_DIM=''; C_RST=''
fi

log()  { printf '%b\n' "${C_BLU}▸${C_RST} $*"; }
ok()   { printf '%b\n' "${C_GRN}✓${C_RST} $*"; }
warn() { printf '%b\n' "${C_YEL}!${C_RST} $*" >&2; }
err()  { printf '%b\n' "${C_RED}✗${C_RST} $*" >&2; }
die()  { err "$*"; exit 1; }
dim()  { printf '%b\n' "${C_DIM}$*${C_RST}"; }

# 解析仓库根目录（_lib.sh 位于 scripts/ 下）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

RUN_DIR="$ROOT_DIR/.run"
LOG_DIR="$ROOT_DIR/logs"
BACKEND_PID="$RUN_DIR/backend.pid"
FRONTEND_PID="$RUN_DIR/frontend.pid"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

ensure_dirs() {
  mkdir -p "$RUN_DIR" "$LOG_DIR"
}

is_alive() {
  # 用法：is_alive <pidfile> ；存在且进程存活返 0
  local pf="$1"
  [[ -f "$pf" ]] || return 1
  local pid
  pid="$(cat "$pf" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

stop_pid() {
  # 用法：stop_pid <pidfile> <name>
  local pf="$1" name="$2"
  if ! [[ -f "$pf" ]]; then
    return 0
  fi
  local pid
  pid="$(cat "$pf" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    log "停止 $name (pid=$pid)"
    kill "$pid" 2>/dev/null || true
    # 优雅关闭最多等 8 秒
    for _ in 1 2 3 4 5 6 7 8; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      warn "$name 未在 8 秒内退出，强杀"
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pf"
}

# 等待 HTTP 端点就绪：wait_http <url> <max_seconds> <name>
wait_http() {
  local url="$1" max="${2:-30}" name="${3:-service}"
  local i=0
  while (( i < max )); do
    if curl -fsS -m 2 "$url" >/dev/null 2>&1; then
      ok "$name 就绪 ($url)"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  err "$name 未在 ${max}s 内就绪 ($url)"
  return 1
}

# 等待 docker compose 服务 healthy：wait_compose_healthy <compose_file> <service> <max_seconds>
wait_compose_healthy() {
  local cf="$1" svc="$2" max="${3:-60}"
  local i=0
  while (( i < max )); do
    local state
    state="$(docker compose -f "$cf" ps --format json "$svc" 2>/dev/null \
              | python3 -c "import sys,json
try:
  data = sys.stdin.read().strip()
  if not data:
    print('missing'); sys.exit()
  # 兼容旧版（一行一对象）和新版（数组）
  if data.startswith('['):
    arr = json.loads(data)
  else:
    arr = [json.loads(l) for l in data.splitlines() if l.strip()]
  if not arr:
    print('missing'); sys.exit()
  print(arr[0].get('Health') or arr[0].get('State') or 'unknown')
except Exception:
  print('error')
" 2>/dev/null)"
    if [[ "$state" == "healthy" || "$state" == "running" ]]; then
      ok "compose:$svc → $state"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  err "compose:$svc 在 ${max}s 内未达健康状态"
  return 1
}

# 通用：检查命令是否存在
need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1（$2）"
}
