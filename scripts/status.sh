#!/usr/bin/env bash
# 状态总览：四个组件分别输出 OK / DOWN
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
cd "$ROOT_DIR"

print_row() {
  local name="$1" status="$2" extra="$3"
  # 注意：format string 必须用双引号，单引号会让 ${C_GRN} 等颜色变量
  # 原样输出而不被 bash 展开（之前的 bug 表现：屏幕上看到字面量 ${C_GRN}）
  case "$status" in
    OK)   printf "  ${C_GRN}● %-12s${C_RST} %s\n" "$name" "$extra" ;;
    WARN) printf "  ${C_YEL}● %-12s${C_RST} %s\n" "$name" "$extra" ;;
    DOWN) printf "  ${C_RED}● %-12s${C_RST} %s\n" "$name" "$extra" ;;
  esac
}

echo
log "组件状态"

# Postgres
if docker compose -f docker-compose.dev.yml ps postgres 2>/dev/null | grep -q "Up"; then
  print_row "Postgres" OK "5432 容器健康"
else
  print_row "Postgres" DOWN "未运行（make up 启动）"
fi

# Redis
if docker compose -f docker-compose.dev.yml ps redis 2>/dev/null | grep -q "Up"; then
  print_row "Redis" OK "6379 容器健康"
else
  print_row "Redis" DOWN "未运行"
fi

# Backend
if is_alive "$BACKEND_PID" && curl -fsS -m 2 http://localhost:8000/healthz >/dev/null 2>&1; then
  print_row "Backend" OK "pid=$(cat "$BACKEND_PID")  http://localhost:8000  /docs 可看 API"
elif is_alive "$BACKEND_PID"; then
  print_row "Backend" WARN "pid=$(cat "$BACKEND_PID") 但 /healthz 不通（可能在启动中）"
else
  print_row "Backend" DOWN "未运行"
fi

# Frontend
if is_alive "$FRONTEND_PID" && curl -fsS -m 2 http://localhost:5173/ >/dev/null 2>&1; then
  print_row "Frontend" OK "pid=$(cat "$FRONTEND_PID")  http://localhost:5173"
elif is_alive "$FRONTEND_PID"; then
  print_row "Frontend" WARN "pid=$(cat "$FRONTEND_PID") 但 :5173 不通"
else
  print_row "Frontend" DOWN "未运行"
fi

echo
