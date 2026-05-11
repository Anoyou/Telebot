// 系统状态卡：把"PostgreSQL/alembic/Redis/providers/proxies/workers"这一堆
// 技术名词翻成大白话——首屏只给"OK / 注意 / 出问题"的人话结论；技术细节藏到
// hover 提示和"展开技术详情"折叠块里。
//
// 数据来自 GET /api/system/health-overview；30s 自动刷新一次。
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight, Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/misc";
import {
  getHealthOverview,
} from "@/api/system";
import { getNetworkInfo, refreshNetworkInfo } from "@/api/network";
import type { HealthOverview, NetworkInfo } from "@/api/types";

// 后端 account.status 枚举的中文标签
const ACCOUNT_STATUS_LABEL: Record<string, string> = {
  active: "运行中",
  paused: "已暂停",
  floodwait: "限流中",
  dead: "已停用",
  login_required: "需重登",
};

type Tone = "ok" | "warn" | "err";

function Dot({ tone }: { tone: Tone }) {
  const cls = {
    ok: "bg-emerald-500",
    warn: "bg-amber-500",
    err: "bg-rose-500",
  }[tone];
  return <span className={`inline-block h-2 w-2 rounded-full ${cls}`} />;
}

// 大白话状态文案——首屏只看这一句就够了
function ToneText({ tone, text }: { tone: Tone; text: string }) {
  const cls = {
    ok: "text-emerald-700 dark:text-emerald-300",
    warn: "text-amber-700 dark:text-amber-300",
    err: "text-rose-700 dark:text-rose-300",
  }[tone];
  return <span className={`text-sm font-medium ${cls}`}>{text}</span>;
}

export function SystemHealthCard() {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["system", "health-overview"],
    queryFn: getHealthOverview,
    // 60s 默认（原 30s）：health-overview 内部并行 6 路探测，对小机器是周期性脉冲。
    // 与 HealthDot 共享 cache key —— 一次请求两个组件复用。
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">系统状态</CardTitle>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? (
              <>
                <ChevronDown className="mr-1 h-3.5 w-3.5" />
                收起
              </>
            ) : (
              <>
                <ChevronRight className="mr-1 h-3.5 w-3.5" />
                展开
              </>
            )}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {q.isLoading ? (
          <div className="flex h-24 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : q.error || !q.data ? (
          <div className="rounded-md border px-3 py-2 text-xs alert-danger">
            读取失败：{(q.error as Error)?.message || "未知错误"}
          </div>
        ) : !open ? (
          <div className="rounded-md border bg-muted/20 px-3 py-2 text-sm text-muted-foreground">
            已收起系统状态详情，点击右上角“展开”查看。
          </div>
        ) : (
          <HealthGrid data={q.data} />
        )}
      </CardContent>
    </Card>
  );
}

function HealthGrid({ data }: { data: HealthOverview }) {
  const dbTone: Tone = data.db.ok ? "ok" : "err";
  const redisTone: Tone = data.redis.ok ? "ok" : "err";
  const alembicTone: Tone = data.alembic.ok
    ? "ok"
    : data.alembic.error
    ? "err"
    : "warn";

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {/* PostgreSQL —— "数据存储" */}
      <HealthBlock
        title="数据存储"
        subtitle="账号、模板、日志、代理等所有配置都存这里"
        tone={dbTone}
        techName="PostgreSQL"
      >
        <ToneText
          tone={dbTone}
          text={data.db.ok ? "✓ 数据库连得上、可读写" : "✗ 连不上数据库"}
        />
        {data.db.ok ? (
          <div className="mt-1 text-xs text-muted-foreground">
            一切正常；这里存了你的全部账号配置和历史。
          </div>
        ) : (
          <div className="mt-1 text-xs text-rose-700 dark:text-rose-300">
            {data.db.error || "连接失败"}
            <br />
            <span className="text-muted-foreground">
              检查 DATABASE_URL；docker 部署的话 <code>docker compose up -d postgres</code>
            </span>
          </div>
        )}
        {data.db.ok && data.db.version ? (
          <TechDetails>{data.db.version}</TechDetails>
        ) : null}
      </HealthBlock>

      {/* Alembic —— "数据库结构" */}
      <HealthBlock
        title="数据库结构"
        subtitle="代码里的表结构是否已经在数据库里建好"
        tone={alembicTone}
        techName="alembic 迁移"
        right={
          <Badge
            variant={data.alembic.ok ? "success" : "warn"}
            className="font-mono text-[10px]"
            title="DB 当前版本 / 代码期望版本"
          >
            {data.alembic.current || "?"}
            {data.alembic.head && data.alembic.head !== data.alembic.current
              ? ` → ${data.alembic.head}`
              : ""}
          </Badge>
        }
      >
        {data.alembic.ok ? (
          <>
            <ToneText tone="ok" text="✓ 数据库结构是最新的" />
            <div className="mt-1 text-xs text-muted-foreground">
              代码 + DB 同版本，无待跑迁移。
            </div>
          </>
        ) : data.alembic.error ? (
          <>
            <ToneText tone="err" text="✗ 探测失败" />
            <div className="mt-1 text-xs text-rose-700 dark:text-rose-300">{data.alembic.error}</div>
          </>
        ) : (
          <>
            <ToneText
              tone="warn"
              text="⚠ 数据库结构落后，等你升级"
            />
            <div className="mt-1 space-y-1 text-xs">
              <div className="text-muted-foreground">
                代码期望 <code>{data.alembic.head}</code>，DB 当前
                <code> {data.alembic.current || "（无）"}</code>。
              </div>
              {data.alembic.pending.length > 0 && (
                <div className="text-muted-foreground">
                  待跑：
                  {data.alembic.pending.map((p) => (
                    <Badge
                      key={p}
                      variant="outline"
                      className="ml-1 font-mono text-xs"
                    >
                      {p}
                    </Badge>
                  ))}
                </div>
              )}
              <div>
                修复：在 backend 跑 <code>alembic upgrade head</code>{" "}
                <span className="text-muted-foreground">
                  （或 <code>make migrate</code>；默认下次 backend 重启会自动升）
                </span>
              </div>
            </div>
          </>
        )}
      </HealthBlock>

      {/* Redis —— "实时通信" */}
      <HealthBlock
        title="实时通信"
        subtitle="主进程和每个账号的 worker 之间靠它互相发指令"
        tone={redisTone}
        techName="Redis"
      >
        <ToneText
          tone={redisTone}
          text={
            data.redis.ok ? "✓ 进程间通信正常" : "✗ Redis 不通，worker 收不到指令"
          }
        />
        {data.redis.ok ? (
          <div className="mt-1 text-xs text-muted-foreground">
            启停账号、热加载模板、风控告警都通这里——通畅就一切如常。
          </div>
        ) : (
          <div className="mt-1 text-xs text-rose-700 dark:text-rose-300">
            {data.redis.error || "PING 失败"}
            <br />
            <span className="text-muted-foreground">
              检查 REDIS_URL；docker 部署的话 <code>docker compose up -d redis</code>
            </span>
          </div>
        )}
      </HealthBlock>

      {/* LLM Providers —— "AI 模型" */}
      <HealthBlock
        title={
          <Link to="/ai" className="hover:underline">
            AI 模型
          </Link>
        }
        subtitle="供 ,ai 命令调用的大语言模型供应商"
        tone={
          data.providers.total === 0
            ? "warn"
            : data.providers.with_api_key < data.providers.total
            ? "warn"
            : "ok"
        }
        techName="LLM Provider"
        right={
          <Badge variant="secondary" className="text-xs">
            共 {data.providers.total} 个
          </Badge>
        }
      >
        {data.providers.total === 0 ? (
          <>
            <ToneText tone="warn" text="⚠ 还没配 AI 模型" />
            <div className="mt-1 text-xs text-muted-foreground">
              想用 <code>,ai</code> 命令？去{" "}
              <Link to="/ai" className="underline">
                AI 设置
              </Link>{" "}
              添加至少一个。
            </div>
          </>
        ) : (
          <>
            <ToneText
              tone={
                data.providers.with_api_key < data.providers.total ? "warn" : "ok"
              }
              text={
                data.providers.with_api_key < data.providers.total
                  ? `⚠ ${data.providers.total - data.providers.with_api_key} 个还没填 api_key`
                  : `✓ ${data.providers.total} 个全部就绪`
              }
            />
            <div className="mt-1 text-xs text-muted-foreground">
              其中{" "}
              <strong className="text-foreground">
                {data.providers.with_proxy}
              </strong>{" "}
              个走代理出网，
              <strong className="text-foreground">
                {data.providers.total - data.providers.with_proxy}
              </strong>{" "}
              个直连。
            </div>
            {Object.keys(data.providers.by_modality).length > 0 && (
              <TechDetails>
                按能力：
                {Object.entries(data.providers.by_modality).map(([m, n]) => (
                  <Badge key={m} variant="outline" className="ml-1 text-xs">
                    {m}:{n}
                  </Badge>
                ))}
              </TechDetails>
            )}
          </>
        )}
      </HealthBlock>

      {/* Proxies —— "代理" */}
      <HealthBlock
        title="代理"
        subtitle="账号 / AI 模型出网时走的转发节点"
        tone={data.proxies.total === 0 ? "warn" : "ok"}
        techName="代理库"
        right={
          <Badge variant="secondary" className="text-xs">
            共 {data.proxies.total} 条
          </Badge>
        }
      >
        {data.proxies.total === 0 ? (
          <>
            <ToneText tone="warn" text="⚠ 代理库为空" />
            <div className="mt-1 text-xs text-muted-foreground">
              如果你在中国大陆访问 OpenAI / Anthropic，需要至少配一条
              socks5/http；去「系统设置 → 代理」加。
            </div>
          </>
        ) : (
          <>
            <ToneText tone="ok" text={`✓ ${data.proxies.total} 条可用`} />
            <div className="mt-1 text-xs text-muted-foreground">
              被 AI 模型引用 <strong>{data.proxies.used_by_llm}</strong> 次。
            </div>
            <TechDetails>
              按类型：
              {Object.entries(data.proxies.by_type).map(([t, n]) => (
                <Badge key={t} variant="outline" className="ml-1 text-xs">
                  {t}:{n}
                </Badge>
              ))}
            </TechDetails>
          </>
        )}
      </HealthBlock>

      {/* Workers / 账号 */}
      <HealthBlock
        title={
          <Link to="/accounts" className="hover:underline">
            账号
          </Link>
        }
        subtitle="每个账号有独立的 worker 子进程在跑"
        tone={
          data.workers.total === 0
            ? "warn"
            : (data.workers.by_status["dead"] ?? 0) > 0 ||
              (data.workers.by_status["login_required"] ?? 0) > 0
            ? "warn"
            : "ok"
        }
        techName="account / worker"
        right={
          <Badge variant="secondary" className="text-xs">
            共 {data.workers.total} 个
          </Badge>
        }
      >
        {data.workers.total === 0 ? (
          <>
            <ToneText tone="warn" text="⚠ 还没绑定任何账号" />
            <div className="mt-1 text-xs text-muted-foreground">
              去「账号」页用向导绑定一个 TG 账号。
            </div>
          </>
        ) : (
          <>
            <ToneText
              tone={
                (data.workers.by_status["dead"] ?? 0) > 0 ||
                (data.workers.by_status["login_required"] ?? 0) > 0
                  ? "warn"
                  : "ok"
              }
              text={(() => {
                const dead = data.workers.by_status["dead"] ?? 0;
                const reauth = data.workers.by_status["login_required"] ?? 0;
                if (dead || reauth) {
                  const parts = [];
                  if (reauth) parts.push(`${reauth} 个需重登`);
                  if (dead) parts.push(`${dead} 个已停用`);
                  return `⚠ ${parts.join("、")}`;
                }
                return `✓ ${data.workers.total} 个账号状态正常`;
              })()}
            />
            <div className="mt-1 flex flex-wrap gap-1.5 text-xs">
              {Object.entries(data.workers.by_status)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([s, n]) => {
                  const variant =
                    s === "active"
                      ? "success"
                      : s === "dead" || s === "login_required"
                      ? "warn"
                      : "outline";
                  return (
                    <Badge key={s} variant={variant} className="text-xs">
                      {ACCOUNT_STATUS_LABEL[s] || s}: {n}
                    </Badge>
                  );
                })}
            </div>
          </>
        )}
      </HealthBlock>

      {/* 主进程出口 —— FastAPI 后端的直连出口 IP/国家。
          注意：TG 账号走的是各自绑定的代理，**不**经过这里——
          那些出口在每张账号卡的"代理"行展示。 */}
      <MainProcessEgressBlock />
    </div>
  );
}

// ── 主进程出口（仅给 LLM fetch / probe 用，与 TG 账号无关）─────────
function MainProcessEgressBlock() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["system", "network"],
    queryFn: getNetworkInfo,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
    refetchIntervalInBackground: false,
  });
  const refreshMut = useMutation({
    mutationFn: refreshNetworkInfo,
    onSuccess: (d: NetworkInfo) =>
      qc.setQueryData(["system", "network"], d),
  });
  const data = q.data;
  const hasError = !!data?.error || (!q.isLoading && !data?.ip);
  const tone: Tone = q.isLoading ? "warn" : hasError ? "warn" : "ok";

  const flag = (() => {
    const c = data?.country;
    if (!c || c.length !== 2) return "🌐";
    const cp = (s: string) => 0x1f1e6 + (s.toUpperCase().charCodeAt(0) - 65);
    try {
      return String.fromCodePoint(cp(c[0]), cp(c[1]));
    } catch {
      return "🌐";
    }
  })();

  return (
    <HealthBlock
      title="主进程出网"
      subtitle="后端进程自己的直连出口（仅用于 ipinfo / LLM fetch 等少量场景）"
      tone={tone}
      techName="main process egress"
      right={
        <Button
          variant="ghost"
          size="sm"
          className="h-6 gap-1 px-2 text-xs"
          disabled={refreshMut.isPending}
          onClick={() => refreshMut.mutate()}
          title="重新探测"
        >
          {refreshMut.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3" />
          )}
        </Button>
      }
    >
      {q.isLoading ? (
        <ToneText tone="warn" text="探测中…" />
      ) : hasError ? (
        <>
          <ToneText tone="warn" text="⚠ 探测失败" />
          <div className="mt-1 text-xs text-muted-foreground break-all">
            {data?.error || "未拿到出口 IP（可能后端无外网）"}
          </div>
        </>
      ) : (
        <>
          <ToneText
            tone="ok"
            text={`✓ ${flag} ${data?.country || "?"}${
              data?.city ? ` · ${data.city}` : ""
            }`}
          />
          <div className="mt-1 space-y-0.5 text-xs text-muted-foreground">
            <div>
              IP <span className="font-mono">{data?.ip || "-"}</span>
              {data?.org ? <span className="ml-2">· {data.org}</span> : null}
            </div>
            <div>
              ⚠ 注意：每个 TG 账号走自己绑定的代理出网，**不**走这里——
              详见上方账号卡的"代理"行。
            </div>
          </div>
        </>
      )}
    </HealthBlock>
  );
}

function HealthBlock({
  title,
  subtitle,
  tone,
  right,
  techName,
  children,
}: {
  title: React.ReactNode;
  /** 一句话解释这块到底是干什么用的（给非技术用户的提示） */
  subtitle?: string;
  tone: Tone;
  right?: React.ReactNode;
  /** 技术名（PostgreSQL / Redis 等）；hover 标题时显示，对懂的人保留入口 */
  techName?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border bg-card/50 p-3" title={techName}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Dot tone={tone} />
          <span>{title}</span>
        </div>
        {right}
      </div>
      {subtitle ? (
        <div className="mb-2 text-xs text-muted-foreground">{subtitle}</div>
      ) : null}
      {children}
    </div>
  );
}

// ── 折叠"技术详情"——给愿意往下钻的人，但不抢首屏注意力 ──
function TechDetails({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        技术详情
      </button>
      {open ? <div className="mt-1 text-xs">{children}</div> : null}
    </div>
  );
}
