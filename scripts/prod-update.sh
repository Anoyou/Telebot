#!/usr/bin/env bash
# Production incremental updater.
#
# Goal:
#   - Pull only fast-forward updates.
#   - Classify changed files before applying.
#   - Rebuild only the Docker Compose services that need new code.
#   - Fall back to full prod-up whenever the change is risky or ambiguous.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
cd "$ROOT_DIR"
export TELEPILOT_HOST_PROJECT_DIR="${TELEPILOT_HOST_PROJECT_DIR:-$ROOT_DIR}"

REMOTE="${TELEPILOT_UPDATE_REMOTE:-origin}"
BRANCH="${TELEPILOT_UPDATE_BRANCH:-main}"
DRY_RUN=0
FORCE_FULL=0
OLD_COMMIT=""

usage() {
  cat <<EOF
用法：scripts/prod-update.sh [--dry-run] [--full]

  --dry-run   只检查远程更新和分类，不拉取、不重建
  --full      强制走完整 make prod-up 路径

可通过环境变量覆盖远程分支：
  TELEPILOT_UPDATE_REMOTE=origin
  TELEPILOT_UPDATE_BRANCH=main
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --full)
      FORCE_FULL=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知参数：$1"
      ;;
  esac
  shift
done

on_error() {
  err "增量更新失败"
  if [[ -n "$OLD_COMMIT" ]]; then
    warn "当前更新前 commit：$OLD_COMMIT"
    warn "如需回滚代码，请人工确认后执行：git checkout $OLD_COMMIT && make prod-up"
  fi
}
trap on_error ERR

need_cmd git "Git 仓库更新"
need_cmd docker "Docker Compose 生产部署"
docker info >/dev/null 2>&1 || die "docker 守护进程未启动"
docker compose version >/dev/null 2>&1 || die "缺 docker compose v2 插件"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 Git 工作树"

REMOTE_REF="refs/remotes/${REMOTE}/${BRANCH}"

log "拉取远程索引 ${REMOTE}/${BRANCH}"
git fetch "$REMOTE" "${BRANCH}:${REMOTE_REF}" >/dev/null

CURRENT_COMMIT="$(git rev-parse HEAD)"
TARGET_COMMIT="$(git rev-parse "$REMOTE_REF")"
OLD_COMMIT="$CURRENT_COMMIT"

if [[ "$CURRENT_COMMIT" == "$TARGET_COMMIT" ]]; then
  ok "当前已是最新 commit：${CURRENT_COMMIT:0:12}"
  exit 0
fi

mapfile -t CHANGED_FILES < <(git diff --name-only "$CURRENT_COMMIT..$TARGET_COMMIT")

NEEDS_BACKEND=0
NEEDS_FRONTEND=0
NEEDS_FULL=0
REQUIRES_BACKUP=0
DOCS_ONLY=1

mark_backend() {
  NEEDS_BACKEND=1
  DOCS_ONLY=0
}

mark_frontend() {
  NEEDS_FRONTEND=1
  DOCS_ONLY=0
}

mark_full() {
  NEEDS_FULL=1
  DOCS_ONLY=0
}

classify_file() {
  local file="$1"
  case "$file" in
    docker-compose.yml|docker-compose.dev.yml|Makefile)
      mark_full
      ;;
    .dockerignore|backend/.dockerignore|frontend/.dockerignore)
      mark_full
      ;;
    backend/Dockerfile|frontend/Dockerfile)
      mark_full
      ;;
    backend/pyproject.toml|frontend/package.json|frontend/pnpm-lock.yaml|frontend/.npmrc)
      mark_full
      ;;
    scripts/_lib.sh|scripts/prod-up.sh|scripts/install-server.sh|scripts/prod-update.sh|scripts/bootstrap.sh)
      mark_full
      ;;
    deploy/*|.github/*)
      mark_full
      ;;
    backend/alembic/versions/*)
      mark_backend
      REQUIRES_BACKUP=1
      ;;
    backend/*|plugins/*)
      mark_backend
      ;;
    frontend/*|CHANGELOG.md|docs/PLUGIN-DEV-GUIDE.md)
      mark_frontend
      ;;
    README.md|CONTRIBUTING.md|LICENSE|AGENTS.md|docs/*|examples/*)
      ;;
    *)
      mark_full
      ;;
  esac
}

for file in "${CHANGED_FILES[@]}"; do
  classify_file "$file"
done

if (( FORCE_FULL == 1 )); then
  NEEDS_FULL=1
  DOCS_ONLY=0
fi

log "更新范围预览"
printf '  当前：%s\n' "${CURRENT_COMMIT:0:12}"
printf '  目标：%s\n' "${TARGET_COMMIT:0:12}"
printf '  文件：%d 个\n' "${#CHANGED_FILES[@]}"
for file in "${CHANGED_FILES[@]:0:30}"; do
  printf '    - %s\n' "$file"
done
if (( ${#CHANGED_FILES[@]} > 30 )); then
  printf '    ... 还有 %d 个文件\n' "$(( ${#CHANGED_FILES[@]} - 30 ))"
fi

if (( NEEDS_FULL == 1 )); then
  warn "分类结果：完整生产更新"
elif (( DOCS_ONLY == 1 )); then
  ok "分类结果：仅文档/说明变更，无需重建服务"
else
  components=()
  (( NEEDS_BACKEND == 1 )) && components+=("web")
  (( NEEDS_FRONTEND == 1 )) && components+=("frontend")
  ok "分类结果：增量更新 ${components[*]}"
fi

if (( REQUIRES_BACKUP == 1 )); then
  warn "本次包含数据库迁移。建议先执行 deploy/backup.sh 或确认已有新备份。"
fi

if (( DRY_RUN == 1 )); then
  ok "dry-run 完成，未拉取代码、未重建服务"
  exit 0
fi

if [[ -n "$(git status --porcelain)" ]]; then
  die "工作区存在未提交改动，拒绝自动更新。请先提交、stash 或清理后重试。"
fi

log "执行 fast-forward 更新"
git pull --ff-only "$REMOTE" "$BRANCH"
NEW_COMMIT="$(git rev-parse HEAD)"
ok "代码已更新到 ${NEW_COMMIT:0:12}"

frontend_url() {
  local raw
  raw="$(grep -E '^WEB_PORT_PUBLISH=' .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d ' "' || true)"
  raw="${raw:-80}"
  if [[ "$raw" == *:* ]]; then
    local host="${raw%:*}"
    local port="${raw##*:}"
    [[ "$host" == "0.0.0.0" || "$host" == "::" ]] && host="127.0.0.1"
    printf 'http://%s:%s' "$host" "$port"
  else
    printf 'http://localhost:%s' "$raw"
  fi
}

if (( NEEDS_FULL == 1 )); then
  if [[ "${TELEPILOT_SKIP_UPDATER_RECREATE:-0}" == "1" ]]; then
    warn "当前由内部 updater 执行完整更新，跳过重建 updater 自身以避免任务被中途杀掉。"
    log "构建 + 启动业务容器（postgres / redis / web / frontend）"
    docker compose up -d --build postgres redis web frontend
    wait_compose_healthy docker-compose.yml postgres 60 || {
      docker compose logs --tail=80 postgres >&2
      exit 1
    }
    wait_compose_healthy docker-compose.yml redis 30 || {
      docker compose logs --tail=80 redis >&2
      exit 1
    }
    wait_compose_healthy docker-compose.yml web 120 || {
      docker compose logs --tail=80 web >&2
      exit 1
    }
    wait_compose_healthy docker-compose.yml frontend 60 || {
      docker compose logs --tail=80 frontend >&2
      exit 1
    }
    wait_http "$(frontend_url)" 30 "前端" || {
      docker compose logs --tail=80 frontend >&2
      exit 1
    }
    ok "完整业务更新完成（updater 保持当前版本）"
  else
    log "执行完整生产更新"
    "$SCRIPT_DIR/prod-up.sh"
  fi
elif (( DOCS_ONLY == 1 )); then
  ok "无需重建服务，更新完成"
else
  services=()
  (( NEEDS_BACKEND == 1 )) && services+=("web")
  (( NEEDS_FRONTEND == 1 )) && services+=("frontend")

  log "增量重建服务：${services[*]}"
  docker compose up -d --build --no-deps "${services[@]}"

  if (( NEEDS_BACKEND == 1 )); then
    wait_compose_healthy docker-compose.yml web 120 || {
      docker compose logs --tail=80 web >&2
      exit 1
    }
  fi

  if (( NEEDS_FRONTEND == 1 )); then
    wait_compose_healthy docker-compose.yml frontend 60 || {
      docker compose logs --tail=80 frontend >&2
      exit 1
    }
    wait_http "$(frontend_url)" 30 "前端" || {
      docker compose logs --tail=80 frontend >&2
      exit 1
    }
  fi

  ok "增量更新完成"
fi

echo
ok "TelePilot 已更新到 ${NEW_COMMIT:0:12}"
