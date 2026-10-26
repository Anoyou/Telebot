#!/usr/bin/env bash
# 在线更新拉取代码前处理工作区未提交改动。
#
# 用法：scripts/prepare-update-worktree.sh [--check-only] [旧提交]
#
#   - 工作区干净：直接返回 0。
#   - 只有可执行位（文件权限）差异：设置仓库级 core.fileMode=false 后返回 0。
#     这类“假脏”来自宿主文件系统/挂载方式无法可靠保存可执行位，stash 之后
#     reset 也写不回去，只会每次更新都重复出现；更新脚本都用 bash 显式调用，
#     不依赖可执行位。
#   - 跟踪文件的内容/权限改动、未被忽略的未跟踪文件：自动
#     git stash push -u -m "telepilot-auto-stash <旧提交> <时间>"，并打印恢复命令。
#   - 处于 merge / rebase / cherry-pick / revert 中、存在冲突、索引被锁或
#     未跟踪文件过大：拒绝更新并列出文件。
#   - --check-only：只列出将要处理的内容，不修改仓库（供 dry-run 使用）。
#
# 被 .gitignore 忽略的文件（.env、plugins/installed、.run 等）从不参与判断，
# 也不会被 stash。

set -euo pipefail

if [[ -t 2 ]]; then
  C_RED=$'\033[0;31m'; C_GRN=$'\033[0;32m'; C_YEL=$'\033[0;33m'; C_BLU=$'\033[0;34m'; C_RST=$'\033[0m'
else
  C_RED=''; C_GRN=''; C_YEL=''; C_BLU=''; C_RST=''
fi
log()  { printf '%b\n' "${C_BLU}▸${C_RST} $*"; }
ok()   { printf '%b\n' "${C_GRN}✓${C_RST} $*"; }
warn() { printf '%b\n' "${C_YEL}!${C_RST} $*" >&2; }
err()  { printf '%b\n' "${C_RED}✗${C_RST} $*" >&2; }

CHECK_ONLY=0
if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=1
  shift
fi
OLD_COMMIT="${1:-$(git rev-parse HEAD)}"
MAX_LIST="${TELEPILOT_DIRTY_LIST_LIMIT:-30}"
MAX_UNTRACKED_BYTES="${TELEPILOT_AUTO_STASH_MAX_UNTRACKED_BYTES:-104857600}"
HOST_DIR="${TELEPILOT_HOST_PROJECT_DIR:-$(pwd)}"

# 先刷新索引里的 stat 缓存：容器内 root 与宿主用户看到的 inode/uid/时间戳
# 不同，只靠 stat 比较会误判；refresh 会按内容重新确认。
git update-index -q --refresh >/dev/null 2>&1 || true

dirty_lines() {
  git -c core.quotePath=false status --porcelain=v1 --untracked-files=normal
}

# 列出前 MAX_LIST 行和总数，供 UI 日志直接展示。
print_dirty_list() {
  local header="$1" total index
  local -a lines=()
  mapfile -t lines < <(dirty_lines)
  total=${#lines[@]}
  warn "${header}（共 ${total} 项，格式同 git status --porcelain）："
  for (( index = 0; index < total && index < MAX_LIST; index++ )); do
    printf '    %s\n' "${lines[$index]}" >&2
  done
  if (( total > MAX_LIST )); then
    printf '    ... 还有 %d 项未列出\n' "$(( total - MAX_LIST ))" >&2
  fi
}

# 给最终错误行用的短摘要：前 5 个路径 + 总数，便于 UI 失败详情一眼看到。
dirty_summary() {
  local total index path
  local -a lines=() names=()
  mapfile -t lines < <(dirty_lines)
  total=${#lines[@]}
  for (( index = 0; index < total && index < 5; index++ )); do
    path="${lines[$index]:3}"
    names+=("$path")
  done
  local joined
  joined="$(IFS='，'; printf '%s' "${names[*]}")"
  if (( total > 5 )); then
    printf '%d 项：%s 等' "$total" "$joined"
  else
    printf '%d 项：%s' "$total" "$joined"
  fi
}

refuse() {
  local reason="$1"
  print_dirty_list "工作区未提交改动"
  err "工作区存在未提交改动，拒绝自动更新：${reason}（$(dirty_summary)）。请在宿主机 ${HOST_DIR} 执行 git status 处理后重试。"
  exit 1
}

if [[ -z "$(dirty_lines)" ]]; then
  ok "工作区干净"
  exit 0
fi

# 1. 无法安全 stash 的状态：进行中的 merge/rebase/cherry-pick/revert、冲突、索引锁。
for marker in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply; do
  marker_path="$(git rev-parse --git-path "$marker")"
  if [[ -e "$marker_path" ]]; then
    refuse "仓库正处于未完成的 ${marker} 状态，自动 stash 可能丢失解决到一半的冲突"
  fi
done
if [[ -n "$(git ls-files --unmerged)" ]]; then
  refuse "存在未解决的合并冲突"
fi
if [[ -e "$(git rev-parse --git-path index.lock)" ]]; then
  refuse "检测到 index.lock，可能有其它 git 进程正在运行"
fi

# 2. 只有可执行位差异：设置 core.fileMode=false，而不是 stash。
filemode="$(git config --bool core.fileMode 2>/dev/null || echo true)"
if [[ "$filemode" != "false" && -z "$(git -c core.fileMode=false status --porcelain=v1 --untracked-files=normal)" ]]; then
  print_dirty_list "检测到只有文件可执行位差异"
  if (( CHECK_ONLY == 1 )); then
    warn "dry-run：正式更新时将设置仓库级 core.fileMode=false，不修改任何文件内容"
    exit 0
  fi
  git config core.fileMode false
  warn "已设置仓库级 core.fileMode=false：这些差异来自宿主文件系统或挂载方式无法可靠保存可执行位，"
  warn "内容没有任何变化；stash 后也无法把权限写回，下次仍会出现。更新脚本均用 bash 显式调用，不依赖可执行位。"
  warn "如需恢复：在宿主机 ${HOST_DIR} 执行 git config --unset core.fileMode"
  if [[ -n "$(dirty_lines)" ]]; then
    refuse "设置 core.fileMode=false 后仍有改动"
  fi
  ok "工作区已干净（仅忽略权限位差异）"
  exit 0
fi

# 3. 未跟踪文件过大时不放进 stash，避免把备份/大文件塞进 .git 撑爆磁盘。
untracked_bytes="$(
  git ls-files --others --exclude-standard -z | python3 -c '
import os, sys
total = 0
for raw in sys.stdin.buffer.read().split(b"\0"):
    if raw:
        try:
            total += os.lstat(raw).st_size
        except OSError:
            pass
print(total)
'
)"
if (( untracked_bytes > MAX_UNTRACKED_BYTES )); then
  refuse "未跟踪文件共 $(( untracked_bytes / 1048576 )) MiB，超过自动 stash 上限 $(( MAX_UNTRACKED_BYTES / 1048576 )) MiB；请移出仓库目录或加入 .git/info/exclude"
fi

print_dirty_list "工作区存在未提交改动"
stash_time="$(python3 -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))')"
stash_name="telepilot-auto-stash ${OLD_COMMIT:0:12} ${stash_time}"
if (( CHECK_ONLY == 1 )); then
  warn "dry-run：正式更新时将自动 git stash push -u -m \"telepilot-auto-stash ${OLD_COMMIT:0:12} <UTC 时间>\" 后继续"
  exit 0
fi

log "自动暂存未提交改动：git stash push -u -m \"${stash_name}\""
# updater 容器里通常没有 git 身份，stash 需要作者信息；只用于这条 stash 提交。
if ! git -c user.name="TelePilot Updater" -c user.email="updater@telepilot.invalid" \
  stash push --include-untracked -m "$stash_name" >&2; then
  refuse "git stash 失败"
fi
stash_commit="$(git rev-parse --verify -q refs/stash || true)"
stash_subject="$(git log -1 --format=%s "$stash_commit" 2>/dev/null || true)"
if [[ -z "$stash_commit" || "$stash_subject" != *"$stash_name"* ]]; then
  refuse "git stash 未生成预期的暂存记录"
fi

warn "已自动暂存工作区改动，未删除任何内容：${stash_name}（${stash_commit:0:12}）"
warn "如需找回，在宿主机 ${HOST_DIR} 执行："
warn "  git stash list                          # 找到 \"${stash_name}\""
warn "  git stash show -p ${stash_commit:0:12}         # 查看暂存内容"
warn "  git stash pop                           # 或 git stash apply ${stash_commit:0:12}；与新版本冲突时按提示解决"

# stash 后若仍只剩权限位差异（文件系统写不回可执行位），按第 2 步处理。
if [[ -n "$(dirty_lines)" ]]; then
  if [[ -z "$(git -c core.fileMode=false status --porcelain=v1 --untracked-files=normal)" ]]; then
    git config core.fileMode false
    warn "stash 后仍有可执行位差异，文件系统无法保存权限位；已设置仓库级 core.fileMode=false"
  else
    refuse "stash 后工作区仍不干净"
  fi
fi
ok "工作区已干净，继续更新"
