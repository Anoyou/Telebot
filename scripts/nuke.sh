#!/usr/bin/env bash
# 彻底清理：停服务 + 删 docker volume + 删 venv + 删 node_modules + 删 .env
# ⚠ 这会清空数据库（账号、规则、session 全没）。慎用。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
cd "$ROOT_DIR"

if [[ "${1:-}" != "--yes" ]]; then
  warn "这会删除：DB 数据卷 + Redis 数据卷 + sessions 卷 + .venv + node_modules + .env"
  echo
  read -r -p "$(echo "${C_YEL}确认彻底清理？输入 'yes' 继续：${C_RST}")" answer
  [[ "$answer" == "yes" ]] || { dim "已取消"; exit 0; }
fi

# 1. 停所有服务（dev + prod 都尝试）
"$SCRIPT_DIR/down.sh" || true
docker compose down -v 2>/dev/null || true
docker compose -f docker-compose.dev.yml down -v 2>/dev/null || true

# 2. 删除 dev volume（compose down -v 应该已删；保险再 rm 一次）
for v in telebot_telebot-pgdata telebot_telebot-redisdata telebot_pgdata telebot_redisdata telebot_sessions; do
  docker volume rm "$v" 2>/dev/null || true
done

# 3. 本机生成物
rm -rf backend/.venv backend/.pytest_cache backend/.ruff_cache
rm -rf frontend/node_modules frontend/dist frontend/.vite
find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# 4. .env 与运行态
rm -f .env
rm -rf "$RUN_DIR" "$LOG_DIR"

ok "已彻底清理"
dim "  下次：${C_GRN}make up${C_RST}${C_DIM} 会从零重建（重新生成 .env + 装依赖）"
