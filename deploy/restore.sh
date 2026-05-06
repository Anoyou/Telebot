#!/usr/bin/env bash
# 还原脚本（占位）：从 backup.sh 产物中恢复数据库 + session volume
# 使用：./deploy/restore.sh <db-YYYYmmdd-HHMM.sql> <sessions-YYYYmmdd-HHMM.tgz>
# ⚠ 还原前必须确保 .env 中的 MASTER_KEY 与备份时一致，否则 session 解密失败。
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "用法：$0 <db-dump.sql> <sessions-archive.tgz>" >&2
    exit 2
fi

DB_DUMP=$1
SESSIONS_ARCHIVE=$2

# 校验文件存在
[[ -f "$DB_DUMP" ]] || { echo "找不到数据库 dump：$DB_DUMP" >&2; exit 1; }
[[ -f "$SESSIONS_ARCHIVE" ]] || { echo "找不到 session 归档：$SESSIONS_ARCHIVE" >&2; exit 1; }

# 加载 .env，与 backup.sh 保持一致
ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
if [[ -f "$ROOT_DIR/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; . "$ROOT_DIR/.env"; set +a
fi

PG_USER=${POSTGRES_USER:-telebot}
PG_DB=${POSTGRES_DB:-telebot}
VOLUME_NAME=${SESSIONS_VOLUME:-telebot_sessions}

read -r -p "确认要把数据库 [$PG_DB] 与 sessions volume [$VOLUME_NAME] 全部覆盖恢复？(yes/N) " ans
[[ "$ans" == "yes" ]] || { echo "已取消"; exit 0; }

echo "[1/3] 停止 web/worker，避免恢复期间写入..."
docker compose stop web || true

echo "[2/3] 还原数据库..."
# 删库重建
docker compose exec -T postgres psql -U "$PG_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS $PG_DB;" -c "CREATE DATABASE $PG_DB;"
docker compose exec -T postgres psql -U "$PG_USER" -d "$PG_DB" < "$DB_DUMP"

echo "[3/3] 还原 sessions volume..."
# 清空原 volume，再解压新内容
ARCHIVE_DIR=$(cd "$(dirname "$SESSIONS_ARCHIVE")" && pwd)
ARCHIVE_NAME=$(basename "$SESSIONS_ARCHIVE")
docker run --rm \
    -v "$VOLUME_NAME":/sessions \
    -v "$ARCHIVE_DIR":/backup:ro \
    alpine \
    sh -c "rm -rf /sessions/* && tar xzf /backup/$ARCHIVE_NAME -C /"

echo "重启 web 服务..."
docker compose start web

echo "✅ 恢复完成。请在 Web 端验证账号状态；如出现 login_required，请检查 MASTER_KEY 是否与备份时一致。"
