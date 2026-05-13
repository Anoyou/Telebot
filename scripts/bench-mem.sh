#!/usr/bin/env bash
# 本地 1G 内存档位 benchmark：复现 / 对比 docker compose 栈在不同分支或档位
# 下的内存与 worker 子进程结构。专为「1C/1G VPS 优化是否真的有效」类比较设计。
#
# 子命令：
#   run        采一轮（默认 300s @ 1Hz）→ bench-out/<label>_<ts>/
#   compare    对比两个采样目录，输出 markdown 表
#   summary    对单个采样目录重新算平均/峰值（run 时已自动生成一份）
#
# 典型流程：
#   1. 在主仓（不是 worktree）准备好 .env（密钥就位、账号 status 设到你想测的状态）
#   2. 切到 baseline 分支：
#        git checkout main
#        scripts/bench-mem.sh run --label main --up --warmup 60 --duration 300
#   3. 切到对比分支：
#        git checkout <your-pr-branch>
#        scripts/bench-mem.sh run --label pr --up --warmup 60 --duration 300
#   4. 对比：
#        scripts/bench-mem.sh compare bench-out/main_*  bench-out/pr_*
#
# 关键设计（为什么不直接复用 prod-up.sh）：
#   - bench 关心「同一份 compose project」在不同分支下的稳态表现，所以强制
#     COMPOSE_PROJECT_NAME=telebot-bench，让 main 与 PR 两轮共享 pgdata /
#     redisdata / sessions 卷，避免 schema/数据不一致引入额外变量。
#   - --up 时只做 prod-up.sh 里跟「资源参数」直接相关的一步：调 auto_tune_env
#     注入档位（PR 分支才有此函数；main 分支跳过并 warn）→ 接着 build + up。
#     跳过密钥/弱密码强校验，方便本地 dev 用占位 MASTER_KEY 跑。
#   - 强制要求在主仓（不在 .claude/worktrees/* 下）运行，避免 worktree 路径
#     基名导致 compose project 漂移、各 worktree 各起一份容器。
#   - docker stats 只采本 compose project 的容器（OrbStack 里同时跑别的容器
#     不会污染数据）。
#   - 采样前 --warmup 60s 等 worker / 连接池 / Redis stream 进入稳态。
#   - runtime_log 行数用「采样开始/结束」时间窗口，不是「过去 5min」。
#   - 每轮结束记录 docker inspect 的 RestartCount / OOMKilled，直接看见
#     OOM 杀容器的情形。

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_lib.sh"

# 强制走主仓而非 worktree（worktree 的目录基名 ≠ telebot，会让 compose
# project name 自动漂移，从而起一套独立容器/卷，数据不可比）。
if [[ "$ROOT_DIR" == */.claude/worktrees/* ]]; then
  die "禁止在 worktree 路径下运行 bench：$ROOT_DIR
请切回主仓（git rev-parse --git-common-dir | xargs dirname）再 git checkout 你想测的分支。"
fi

cd "$ROOT_DIR"

# 给本 bench 固定一个 compose project name，让 main / PR 两轮共享同一套
# pgdata / redisdata / sessions 卷。可通过环境变量覆盖（如想隔离一组数据）。
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-telebot-bench}"

OUT_ROOT="$ROOT_DIR/bench-out"

# ── 工具函数 ────────────────────────────────────────────────

to_mib() {
  # 把 "358.5MiB" / "1GiB" / "1024KiB" / "1.05GB" 统一转 MiB（浮点保留 1 位）
  python3 - "$1" <<'PY'
import sys, re
v = sys.argv[1].strip()
m = re.match(r"^([0-9.]+)\s*([KMG])i?B$", v, re.I)
if not m:
    print("0.0"); sys.exit()
n = float(m.group(1)); u = m.group(2).upper()
mib = {"K": n/1024, "M": n, "G": n*1024}[u]
print(f"{mib:.1f}")
PY
}

compose_ps_id() {
  docker compose ps -q "$1" 2>/dev/null | head -n1
}

compose_all_ids() {
  # 当前 compose project 下所有容器 ID（含 stopped 也无所谓，stats 只对 running 有效）
  docker compose ps -q 2>/dev/null | tr '\n' ' '
}

# 等容器健康/运行。docker compose v2 在不同版本输出格式略有差异，统一兜底。
wait_web_healthy() {
  log "等待 web 健康（含 alembic 迁移，最多 180s）..."
  local i=0
  while (( i < 60 )); do
    local state
    state="$(docker compose ps --format json web 2>/dev/null \
              | python3 -c "import sys,json
d=sys.stdin.read().strip()
if not d:
  print('missing'); sys.exit()
arr=json.loads(d) if d.startswith('[') else [json.loads(l) for l in d.splitlines() if l.strip()]
print((arr[0].get('Health') or arr[0].get('State') or 'unknown') if arr else 'missing')" 2>/dev/null)"
    [[ "$state" == "healthy" ]] && { ok "web healthy"; return 0; }
    sleep 3
    i=$((i + 1))
  done
  die "web 在 180s 内未健康"
}

ensure_stack() {
  # --up 时跑：先 auto_tune_env（PR 分支才有），再 build + up + 等 healthy。
  # 不 --up 时假设外部已 up 好，仅等 healthy。
  local rebuild="${1:-no}"
  if [[ "$rebuild" == "yes" ]]; then
    if declare -f auto_tune_env >/dev/null 2>&1; then
      log "auto_tune_env .env（PR 路径：按宿主 RAM 自动注入档位）"
      auto_tune_env .env
    else
      warn "当前分支没有 auto_tune_env 函数（baseline / main 路径），跳过自适应注入"
    fi
    log "docker compose up -d --build（project=$COMPOSE_PROJECT_NAME）"
    docker compose up -d --build
  elif [[ -z "$(compose_ps_id web)" ]]; then
    log "栈未启动且未传 --up，自动起一次（不重建镜像）"
    docker compose up -d
  fi
  wait_compose_healthy docker-compose.yml postgres 90 || die "postgres 不健康"
  wait_compose_healthy docker-compose.yml redis 60 || die "redis 不健康"
  wait_web_healthy
}

snapshot_branch_meta() {
  local outfile="$1"
  {
    echo "## branch & env"
    echo "  branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
    echo "  commit: $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
    echo "  compose_project: $COMPOSE_PROJECT_NAME"
    echo "  date: $(date -Iseconds)"
    local tier
    tier="$(grep -E '^MEMORY_TIER=' .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d ' "')"
    echo "  MEMORY_TIER: ${tier:-(unset)}"
    # 顺便记下当前 docker 引擎与 cgroup 版本（cgroup v1 vs v2 的 memcg 行为差异会
    # 影响 docker stats 报数；最好确保两轮在同一环境跑）
    local docker_v cgroup
    docker_v="$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"
    cgroup="$(docker info --format '{{.CgroupVersion}}' 2>/dev/null || echo '?')"
    echo "  docker_engine: $docker_v"
    echo "  cgroup_version: $cgroup"
  } > "$outfile"
}

snapshot_accounts() {
  local outfile="$1"
  local pg
  pg="$(compose_ps_id postgres)"
  {
    echo "## DB 账号状态分布（采样时刻：$(date -Iseconds)）"
    if [[ -n "$pg" ]]; then
      docker exec "$pg" psql -U "${POSTGRES_USER:-telebot}" -d "${POSTGRES_DB:-telebot}" \
        -tAc "SELECT status, count(*) FROM account GROUP BY status ORDER BY status;" 2>/dev/null \
        | awk -F'|' '{ printf "  %s: %s\n", $1, $2 }'
    else
      echo "  (postgres 容器未找到)"
    fi
  } > "$outfile"
}

snapshot_processes() {
  local outfile="$1"
  local tag="$2"
  local web
  web="$(compose_ps_id web)"
  {
    echo "=== timestamp=$(date -Iseconds) tag=$tag ==="
    if [[ -z "$web" ]]; then
      echo "(web 容器未找到)"
      return
    fi
    echo "--- web 容器进程（top RSS）---"
    if ! docker exec "$web" ps -eo pid,rss,etime,comm,args --sort=-rss 2>/dev/null | head -30; then
      echo "(ps 不可用，fallback 到 /proc)"
      docker exec "$web" sh -c '
        for s in /proc/[0-9]*/status; do
          pid=${s#/proc/}; pid=${pid%/status}
          rss=$(awk "/^VmRSS:/{print \$2}" "$s" 2>/dev/null || echo 0)
          name=$(awk "/^Name:/{print \$2}" "$s" 2>/dev/null || echo "?")
          cmd=$(tr "\0" " " < "/proc/$pid/cmdline" 2>/dev/null || true)
          printf "%s %s %s %s\n" "${rss:-0}" "$pid" "$name" "$cmd"
        done | sort -nr | head -30 | awk "{printf \"pid=%s rss_kb=%s name=%s cmd=\", \$2, \$1, \$3; for (i=4;i<=NF;i++) printf \"%s \", \$i; print \"\"}"
      ' 2>/dev/null || true
    fi
    echo
    echo "--- worker 子进程数（multiprocessing.spawn / app.worker / worker_main / worker_entry）---"
    local count
    count="$(docker exec "$web" sh -c '
      if command -v ps >/dev/null 2>&1; then
        ps -eo args
      else
        for p in /proc/[0-9]*/cmdline; do
          tr "\0" " " < "$p" 2>/dev/null
          echo
        done
      fi
    ' 2>/dev/null \
      | grep -E 'multiprocessing\.spawn|app\.worker\.runtime|worker_main|worker_entry' \
      | grep -v grep | wc -l | tr -d ' ')"
    echo "  count=$count"
  } >> "$outfile"
}

snapshot_runtime_log_window() {
  # 采样窗口内 runtime_log 行数。start/end_ts 为 epoch 秒。
  local outfile="$1" start_ts="$2" end_ts="$3"
  local pg
  pg="$(compose_ps_id postgres)"
  {
    echo "## runtime_log（采样窗口 $start_ts → $end_ts，$((end_ts - start_ts))s）"
    if [[ -n "$pg" ]]; then
      local sql="SELECT
        count(*) AS total,
        count(*) FILTER (WHERE level IN ('warn','warning')) AS warn,
        count(*) FILTER (WHERE level = 'error') AS err
        FROM runtime_log
        WHERE ts >= to_timestamp($start_ts) AND ts < to_timestamp($end_ts);"
      local row
      row="$(docker exec "$pg" psql -U "${POSTGRES_USER:-telebot}" -d "${POSTGRES_DB:-telebot}" \
              -tA -F'|' -c "$sql" 2>/dev/null)"
      local total warn err
      total="$(echo "$row" | cut -d'|' -f1 | tr -d ' ')"
      warn="$(echo "$row"  | cut -d'|' -f2 | tr -d ' ')"
      err="$(echo "$row"   | cut -d'|' -f3 | tr -d ' ')"
      printf "  total=%s warn=%s error=%s\n" "${total:-?}" "${warn:-?}" "${err:-?}"
    else
      echo "  (postgres 容器未找到)"
    fi
  } > "$outfile"
}

snapshot_inspect_state() {
  # 采样结束时记录每个容器的 RestartCount / OOMKilled / Status / StartedAt
  local outfile="$1"
  {
    echo "## container state（$(date -Iseconds)）"
    local ids
    ids="$(compose_all_ids)"
    [[ -z "$ids" ]] && { echo "  (无容器)"; return; }
    for cid in $ids; do
      docker inspect "$cid" --format \
        '  {{.Name}}  Restart={{.RestartCount}}  OOMKilled={{.State.OOMKilled}}  Status={{.State.Status}}  StartedAt={{.State.StartedAt}}' \
        2>/dev/null
    done
  } > "$outfile"
}

# ── 主采样循环（只采本 compose project 容器）────────────────

sample_loop() {
  local outdir="$1"
  local dur="$2"
  log "采样 ${dur}s 内存/CPU（仅 project=$COMPOSE_PROJECT_NAME 的容器）→ $outdir/stats.csv"
  echo "ts,container,cpu_pct,mem_mib,mem_limit_mib,mem_pct" > "$outdir/stats.csv"

  local end_at samples=0
  end_at=$(( $(date +%s) + dur ))
  while (( $(date +%s) < end_at )); do
    local ts ids
    ts="$(date +%s)"
    ids="$(compose_all_ids)"
    if [[ -z "$ids" ]]; then
      warn "本 compose project 当前无容器，等 2s 后重试"
      sleep 2; continue
    fi
    # docker stats 接受多 id；--no-stream 一次性返回（约 1-2s）
    while IFS='|' read -r name cpu memusage mempct; do
      [[ -z "$name" ]] && continue
      local used_raw lim_raw used_mib lim_mib
      used_raw="$(echo "$memusage" | awk -F'/' '{print $1}' | sed 's/[[:space:]]//g')"
      lim_raw="$(echo "$memusage"  | awk -F'/' '{print $2}' | sed 's/[[:space:]]//g')"
      used_mib="$(to_mib "$used_raw")"
      lim_mib="$(to_mib "$lim_raw")"
      printf "%s,%s,%s,%s,%s,%s\n" \
        "$ts" "$name" "${cpu%\%}" "$used_mib" "$lim_mib" "${mempct%\%}" >> "$outdir/stats.csv"
    done < <(docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}' $ids 2>/dev/null)
    samples=$((samples + 1))
  done
  ok "采样完成（${samples} 轮 docker stats）"
}

# ── 子命令：run ─────────────────────────────────────────────

cmd_run() {
  local label="bench"
  local duration=300
  local warmup="${WARMUP_SEC:-60}"
  local rebuild="no"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --label)    label="$2"; shift 2 ;;
      --duration) duration="$2"; shift 2 ;;
      --warmup)   warmup="$2"; shift 2 ;;
      --up)       rebuild="yes"; shift ;;
      -h|--help)
        echo "用法：bench-mem.sh run --label <name> [--duration 300] [--warmup 60] [--up]"
        exit 0 ;;
      *) die "未知参数：$1" ;;
    esac
  done

  ensure_stack "$rebuild"

  local ts outdir
  ts="$(date +%Y%m%d_%H%M%S)"
  outdir="$OUT_ROOT/${label}_${ts}"
  mkdir -p "$outdir"
  log "输出目录：$outdir"

  # 0) 元数据 + 入栏快照（healthy 之后但 warmup 之前）
  snapshot_branch_meta "$outdir/meta.txt"
  snapshot_accounts "$outdir/accounts.txt"
  snapshot_inspect_state "$outdir/state_before.txt"
  snapshot_processes "$outdir/ps_web.txt" "before-warmup"

  # 1) warmup：等 worker 拉起 / 连接池热身 / 缓存稳态
  if (( warmup > 0 )); then
    log "warmup ${warmup}s（让 worker / 连接池 / Redis stream 进入稳态）"
    sleep "$warmup"
  fi
  snapshot_processes "$outdir/ps_web.txt" "after-warmup"

  # 2) 主采样
  local start_ts end_ts
  start_ts="$(date +%s)"
  sample_loop "$outdir" "$duration"
  end_ts="$(date +%s)"
  echo "$start_ts $end_ts" > "$outdir/sample_window.txt"

  # 3) 出栏快照
  snapshot_processes "$outdir/ps_web.txt" "after-sample"
  snapshot_inspect_state "$outdir/state_after.txt"
  snapshot_runtime_log_window "$outdir/runtime_log.txt" "$start_ts" "$end_ts"

  # 4) summary
  cmd_summary "$outdir" --quiet
  echo
  cat "$outdir/summary.md"
  echo
  ok "完成 → $outdir"
}

# ── 子命令：summary（单目录） ────────────────────────────────

cmd_summary() {
  local outdir="$1"
  local quiet="no"
  [[ "$2" == "--quiet" ]] && quiet="yes"
  [[ -d "$outdir" ]] || die "目录不存在：$outdir"
  [[ -f "$outdir/stats.csv" ]] || die "缺少 stats.csv：$outdir"

  python3 - "$outdir" <<'PY' > "$outdir/summary.md"
import csv, sys, pathlib, collections
d = pathlib.Path(sys.argv[1])
rows = list(csv.DictReader(open(d/"stats.csv")))
by_c = collections.defaultdict(lambda: {"mem": [], "cpu": [], "limit": []})
for r in rows:
    c = r["container"]
    by_c[c]["mem"].append(float(r["mem_mib"]))
    by_c[c]["cpu"].append(float(r["cpu_pct"] or 0))
    by_c[c]["limit"].append(float(r["mem_limit_mib"]))

print(f"# bench summary: {d.name}\n")

def read_text_safe(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

for fn in ("meta.txt", "accounts.txt", "runtime_log.txt"):
    p = d / fn
    if p.exists():
        print(read_text_safe(p)); print()

ps = d / "ps_web.txt"
if ps.exists():
    txt = read_text_safe(ps)
    # 提取每段 "=== tag=X ===" 之后的 "count=N"
    sections = []
    cur_tag = None
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("=== ") and "tag=" in s:
            cur_tag = s.split("tag=", 1)[1].rstrip("=").strip()
        elif s.startswith("count="):
            sections.append((cur_tag or "?", s.split("=", 1)[1]))
    if sections:
        print("## web 容器内 worker 子进程数（multiprocessing.spawn / app.worker）")
        for tag, c in sections:
            print(f"  {tag}: {c}")
        print()

for fn in ("state_before.txt", "state_after.txt"):
    p = d / fn
    if p.exists():
        print(read_text_safe(p)); print()

print("## 容器内存 / CPU 平均与峰值\n")
print("| 容器 | 样本 | 平均内存 | 峰值内存 | 平均 CPU% | 内存上限 | 峰值占限 |")
print("|---|---:|---:|---:|---:|---:|---:|")
for c in sorted(by_c):
    mem = by_c[c]["mem"]; cpu = by_c[c]["cpu"]; lim = by_c[c]["limit"]
    if not mem: continue
    avg = sum(mem)/len(mem); peak = max(mem); avgc = sum(cpu)/len(cpu)
    lim_mib = max(lim) if lim else 0
    pct = f"{peak/lim_mib*100:.0f}%" if lim_mib > 0 else "-"
    lim_s = f"{lim_mib:.0f} MiB" if lim_mib > 0 else "无限制"
    print(f"| {c} | {len(mem)} | {avg:.1f} MiB | {peak:.1f} MiB | {avgc:.2f} | {lim_s} | {pct} |")
PY

  [[ "$quiet" == "yes" ]] || cat "$outdir/summary.md"
}

# ── 子命令：compare ─────────────────────────────────────────

cmd_compare() {
  local a="$1" b="$2"
  [[ -d "$a" && -d "$b" ]] || die "用法：bench-mem.sh compare DIR_A DIR_B"
  [[ -f "$a/stats.csv" && -f "$b/stats.csv" ]] || die "两个目录都需含 stats.csv"

  python3 - "$a" "$b" <<'PY'
import csv, sys, pathlib, collections

def read_text_safe(p):
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

def load(d):
    rows = list(csv.DictReader(open(pathlib.Path(d)/"stats.csv")))
    by_c = collections.defaultdict(list)
    for r in rows:
        by_c[r["container"]].append(float(r["mem_mib"]))
    return {c: (sum(v)/len(v), max(v), len(v)) for c, v in by_c.items()}

def read_meta(d):
    p = pathlib.Path(d)/"meta.txt"
    return read_text_safe(p)

def worker_counts(d):
    p = pathlib.Path(d)/"ps_web.txt"
    if not p.exists(): return {}
    out = {}; cur = None
    for line in read_text_safe(p).splitlines():
        s = line.strip()
        if s.startswith("=== ") and "tag=" in s:
            cur = s.split("tag=", 1)[1].rstrip("=").strip()
        elif s.startswith("count="):
            out[cur or "?"] = s.split("=", 1)[1]
    return out

def read_state(d):
    p = pathlib.Path(d)/"state_after.txt"
    return read_text_safe(p)

A = load(sys.argv[1]); B = load(sys.argv[2])
print("# 对比 A vs B\n")
for label, dirpath in (("A", sys.argv[1]), ("B", sys.argv[2])):
    print(f"## {label}：`{dirpath}`")
    print("```")
    print(read_meta(dirpath).strip())
    print()
    print("worker 子进程数：")
    for tag, c in worker_counts(dirpath).items():
        print(f"  {tag}: {c}")
    print()
    print(read_state(dirpath).strip())
    print("```\n")

all_c = sorted(set(A) | set(B))
print("## 容器内存平均/峰值对比\n")
print("| 容器 | A 平均 | A 峰值 | B 平均 | B 峰值 | Δ 平均 | Δ 峰值 |")
print("|---|---:|---:|---:|---:|---:|---:|")
def fmt(x): return f"{x:.1f}" if x is not None else "-"
def fmt_d(x):
    if x is None: return "-"
    return f"{x:+.1f}"
for c in all_c:
    a = A.get(c); b = B.get(c)
    a_avg = a[0] if a else None
    a_pk  = a[1] if a else None
    b_avg = b[0] if b else None
    b_pk  = b[1] if b else None
    d_avg = (b_avg - a_avg) if (a_avg is not None and b_avg is not None) else None
    d_pk  = (b_pk - a_pk) if (a_pk is not None and b_pk is not None) else None
    print(f"| {c} | {fmt(a_avg)} | {fmt(a_pk)} | {fmt(b_avg)} | {fmt(b_pk)} | {fmt_d(d_avg)} | {fmt_d(d_pk)} |")
PY
}

# ── 入口 ─────────────────────────────────────────────────────

main() {
  local cmd="${1:-}"
  [[ -z "$cmd" ]] && {
    cat <<EOF
用法：
  scripts/bench-mem.sh run --label <name> [--duration 300] [--warmup 60] [--up]
  scripts/bench-mem.sh summary <dir>
  scripts/bench-mem.sh compare <dirA> <dirB>

环境变量：
  COMPOSE_PROJECT_NAME  默认 telebot-bench；让两轮共享 volume
  WARMUP_SEC            等价 --warmup（默认 60）

典型流程（在主仓目录，不在 worktree！）：
  cd /Users/anoyou/Desktop/telebot

  # 在 baseline 分支：
  git checkout main
  scripts/bench-mem.sh run --label main --up --warmup 60 --duration 300

  # 在 PR 分支：
  git checkout <your-pr-branch>
  scripts/bench-mem.sh run --label pr --up --warmup 60 --duration 300

  # 对比：
  scripts/bench-mem.sh compare bench-out/main_*  bench-out/pr_*
EOF
    exit 0
  }
  shift
  case "$cmd" in
    run)     cmd_run "$@" ;;
    summary) cmd_summary "$@" ;;
    compare) cmd_compare "$@" ;;
    -h|--help|help) main ;;
    *) die "未知子命令：$cmd（用 -h 看用法）" ;;
  esac
}

main "$@"
