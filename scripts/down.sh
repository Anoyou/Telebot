#!/usr/bin/env bash
# 一键停止本机开发环境：
#   - 杀后端 uvicorn / 前端 vite（按 .run/*.pid）
#   - 杀掉所有"工作目录在 telebot/"的孤儿 multiprocessing worker
#     （uvicorn --reload 重启主进程时会留下 spawn 出去的孤儿 worker，
#      ppid 变成 1，跟主进程脱钩——下次 make up 它们仍跑老代码！）
#   - docker compose -f docker-compose.dev.yml down（保留卷，下次 up 接着用）
#
# 数据 / .env / venv / node_modules 全部保留。如要彻底清理用 `make clean`。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
cd "$ROOT_DIR"

# 杀本机进程
stop_pid "$BACKEND_PID"  "后端 uvicorn"
stop_pid "$FRONTEND_PID" "前端 vite"

# 兜底：按端口杀（uvicorn --reload 会 fork 一个 reloader 父进程 + worker；
# 父进程退出时 worker 也应该走，但保险起见把残留 8000/5173 占用都收一遍）
kill_by_port() {
  local port="$1" name="$2"
  local pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    warn "$name 仍占用 :$port (pid=$pids)，强杀"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}
kill_by_port 8000 "后端残留"
kill_by_port 5173 "前端残留"

# 关键：清理 telebot 派生的孤儿 multiprocessing worker
# 场景：uvicorn --reload 重启主进程时，supervisor 在 lifespan 关闭里 stop_all_workers
# 但有时来不及，spawn 出的 worker 子进程会被 init (pid 1) 接管成孤儿。
# 这些孤儿仍连着 Redis pubsub 处理 TG 消息，但跑的是**旧代码**——
# 用户改完代码后看到 ai 命令响应永远是几小时前的逻辑。
#
# 检测方式：**用 lsof 看进程打开的文件**，含 telebot/backend 路径就视为本项目派生。
# 不能用 cwd——init 接管孤儿时会把 cwd 重置成 ``/``，cwd 检测对真正的孤儿失效。
# 而 worker 进程在跑时会持有 .py 文件 fd（包括 mmap 的 cython 模块、socket 等），
# lsof 出来含 ``telebot/backend`` 字样几乎一定是本项目的进程。
kill_orphan_telebot_workers() {
  local pids killed=()
  pids="$(pgrep -f 'multiprocessing.spawn' 2>/dev/null || true)"
  [[ -z "$pids" ]] && return 0
  local pid
  for pid in $pids; do
    # lsof 列所有打开的文件；如果有任何条目含 telebot/backend 路径，判定为本项目进程
    if lsof -p "$pid" 2>/dev/null | grep -q "telebot/backend"; then
      kill -9 "$pid" 2>/dev/null || true
      killed+=("$pid")
    fi
  done
  if (( ${#killed[@]} > 0 )); then
    warn "杀掉 telebot 孤儿 worker（这些跑老代码会让 ,ai 响应不对）: ${killed[*]}"
  fi
}
kill_orphan_telebot_workers

# 关 docker pg+redis（保留 volume）
log "停止 PostgreSQL + Redis 容器（数据保留）"
docker compose -f docker-compose.dev.yml down >/dev/null 2>&1 || true

ok "全部停止"
dim "  数据卷保留；下次 ${C_GRN}make up${C_RST}${C_DIM} 接着用"
dim "  彻底清理（含数据库）：${C_GRN}make nuke${C_RST}"
