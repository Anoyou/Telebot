#!/usr/bin/env bash
# 每日备份脚本：dump 数据库 + 打包 session volume
# 使用：在仓库根目录执行 ./deploy/backup.sh
# 推荐 cron：0 3 * * * cd /opt/telebot && ./deploy/backup.sh >> /var/log/telebot-backup.log 2>&1
set -euo pipefail

# 时间戳与目标目录
TS=$(date +%Y%m%d-%H%M)
DIR=${BACKUP_DIR:-/var/backups/telebot}

# 数据库账号（与 docker-compose.yml 一致，可被环境变量覆盖）
PG_USER=${POSTGRES_USER:-telebot}
PG_DB=${POSTGRES_DB:-telebot}

# 加载仓库根目录的 .env，便于读取 POSTGRES_USER / POSTGRES_DB
ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
if [[ -f "$ROOT_DIR/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; . "$ROOT_DIR/.env"; set +a
    PG_USER=${POSTGRES_USER:-$PG_USER}
    PG_DB=${POSTGRES_DB:-$PG_DB}
fi

mkdir -p "$DIR"

echo "[$(date)] 开始备份到 $DIR ..."

# 1. PostgreSQL 全量 dump（在 postgres 容器内执行 pg_dump）
docker compose exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" --no-owner > "$DIR/db-$TS.sql"

# 2. session volume 打包（用临时 alpine 容器挂载 telebot_sessions 卷）
#    注意：volume 默认前缀 = 项目目录名，标准是 telebot_sessions；如自定义 COMPOSE_PROJECT_NAME，请相应调整
VOLUME_NAME=${SESSIONS_VOLUME:-telebot_sessions}
docker run --rm \
    -v "$VOLUME_NAME":/sessions:ro \
    -v "$DIR":/backup \
    alpine \
    tar czf "/backup/sessions-$TS.tgz" -C / sessions

# 3. 简单清理：保留最近 30 天
find "$DIR" -name 'db-*.sql' -mtime +30 -delete || true
find "$DIR" -name 'sessions-*.tgz' -mtime +30 -delete || true

echo "[$(date)] 备份完成："
echo "  - $DIR/db-$TS.sql"
echo "  - $DIR/sessions-$TS.tgz"
echo "⚠ 注意：还原前必须确保 .env 中 MASTER_KEY 与备份时一致，否则 session 解密失败！"
