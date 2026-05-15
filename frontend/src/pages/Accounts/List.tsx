// 账号列表：卡片网格形式（移动端单列），含启停 / 详情 / 删除（二次确认）操作
import { useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Package,
  Plus,
  Power,
  Sparkles,
  Trash2,
  Wand,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/misc";
import { AccountSummaryCard } from "@/components/AccountSummaryCard";
import {
  deleteAccount,
  listAccounts,
  pauseAccount,
  resumeAccount,
} from "@/api/accounts";
import { getErrMsg } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";

type GuideStep = {
  icon: typeof Plus;
  title: string;
  desc: string;
  actionLabel: string;
  actionTo: string;
};

const GUIDE_STEPS: GuideStep[] = [
  {
    icon: Plus,
    title: "1. 添加并启用账号",
    desc: "先新增 Telegram 账号并启用它，系统会为该账号启动独立 worker。",
    actionLabel: "去添加账号",
    actionTo: "/accounts/new",
  },
  {
    icon: Wand,
    title: "2. 设置命令前缀",
    desc: "在系统设置里确定命令开头字符，比如 ,ai。",
    actionLabel: "去设置前缀",
    actionTo: "/settings?tab=platform",
  },
  {
    icon: Package,
    title: "3. 启用命令模板或调用插件",
    desc: "去插件中心启用模板或插件，然后就能在 Telegram 里直接调用。",
    actionLabel: "去插件中心",
    actionTo: "/plugins",
  },
];

function getGuideStepByPath(pathname: string, search: string): number {
  if (pathname === "/accounts" || pathname === "/accounts/new") return 0;
  if (pathname === "/settings" && new URLSearchParams(search).get("tab") === "platform") return 1;
  if (pathname === "/plugins" || pathname.startsWith("/plugins/")) return 2;
  return 0;
}

export function AccountList() {
  const nav = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const qc = useQueryClient();
  const [guideLauncherOpen, setGuideLauncherOpen] = useState(false);
  const [guideExpanded, setGuideExpanded] = useState(false);
  const guideActive = searchParams.get("guide") === "1";
  const currentStep = useMemo(
    () => getGuideStepByPath(location.pathname, location.search),
    [location.pathname, location.search],
  );

  const { data, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  const toggleMut = useMutation({
    mutationFn: async (vars: { aid: number; pause: boolean }) =>
      vars.pause ? pauseAccount(vars.aid) : resumeAccount(vars.aid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已下发指令");
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: deleteAccount,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已删除账号");
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  function startGuide() {
    const next = new URLSearchParams(searchParams);
    next.set("guide", "1");
    setSearchParams(next);
    setGuideLauncherOpen(false);
    setGuideExpanded(false);
  }

  function stopGuide() {
    const next = new URLSearchParams(searchParams);
    next.delete("guide");
    setSearchParams(next);
    setGuideLauncherOpen(false);
    setGuideExpanded(false);
  }

  return (
    <div className="space-y-6 pb-24">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">账号管理</h1>
          <p className="text-sm text-muted-foreground">
            每个账号 = 一个 session = 一个独立 worker 进程
          </p>
        </div>
        <div className="relative flex flex-col items-stretch gap-2 sm:items-end">
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              className={guideActive ? "siri-glow" : undefined}
              onClick={() => setGuideLauncherOpen((v) => !v)}
              aria-label="打开新手指引"
              title="新手指引"
            >
              <Sparkles className="mr-1 h-4 w-4 text-primary" />
              新手指引
            </Button>
            <Button
              className={guideActive && currentStep === 0 ? "siri-glow" : undefined}
              onClick={() => nav("/accounts/new")}
            >
              <Plus className="mr-1 h-4 w-4" /> 新增账号
            </Button>
          </div>
          {guideLauncherOpen ? (
            <GuideLauncher
              active={guideActive}
              onStart={startGuide}
              onStop={stopGuide}
              onClose={() => setGuideLauncherOpen(false)}
            />
          ) : null}
          {guideActive ? (
            <GuideContextCard
              expanded={guideExpanded}
              currentStep={currentStep}
              onToggle={() => setGuideExpanded((v) => !v)}
              onGo={() => nav(`${GUIDE_STEPS[currentStep].actionTo}${GUIDE_STEPS[currentStep].actionTo.includes("?") ? "&" : "?"}guide=1`)}
              onSkip={() => {
                const nextStep = GUIDE_STEPS[Math.min(currentStep + 1, GUIDE_STEPS.length - 1)];
                nav(`${nextStep.actionTo}${nextStep.actionTo.includes("?") ? "&" : "?"}guide=1`);
              }}
            />
          ) : null}
        </div>
      </div>

      {isLoading ? (
        <div className="flex h-32 items-center justify-center">
          <Spinner className="text-primary" />
        </div>
      ) : data && data.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {data.map((a) => (
            <AccountSummaryCard
              key={a.id}
              account={a}
              footer={
                <div className="space-y-2 text-xs">
                  <div className="flex items-center justify-between text-muted-foreground">
                    <span>已启用 {a.enabled_features} 项</span>
                    <span title={formatDateTime(a.created_at)}>
                      {formatDateTime(a.created_at).slice(0, 10)}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 px-2"
                      onClick={() =>
                        toggleMut.mutate({
                          aid: a.id,
                          pause: a.status === "active",
                        })
                      }
                    >
                      <Power className="mr-1 h-3.5 w-3.5" />
                      {a.status === "active" ? "暂停" : "启动"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 px-2"
                      onClick={() => nav(`/accounts/${a.id}`)}
                    >
                      详情
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 px-2 text-destructive hover:text-destructive"
                      onClick={() => {
                        const label =
                          a.display_name ||
                          (a.tg_username ? `@${a.tg_username}` : `#${a.id}`);
                        if (
                          confirm(
                            `确认删除账号 ${label}？此操作会撤销 session 并清空配置。`,
                          )
                        )
                          delMut.mutate(a.id);
                      }}
                    >
                      <Trash2 className="mr-1 h-3.5 w-3.5" /> 删除
                    </Button>
                  </div>
                </div>
              }
            />
          ))}
        </div>
      ) : (
        <p className="rounded-lg border bg-card py-12 text-center text-sm text-muted-foreground">
          尚未绑定账号，
          <Link to="/accounts/new" className="text-primary hover:underline">
            立即新增
          </Link>
        </p>
      )}
    </div>
  );
}

function GuideLauncher({
  active,
  onStart,
  onStop,
  onClose,
}: {
  active: boolean;
  onStart: () => void;
  onStop: () => void;
  onClose: () => void;
}) {
  return (
    <div className="absolute right-0 top-full z-40 mt-2 w-[19rem] rounded-2xl border bg-card/95 p-4 text-left shadow-xl backdrop-blur">
      <div className="mb-1 text-sm font-semibold">开启新手指引模式？</div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        开启后会在当前页面用小条提示下一步，并用七彩流光高亮你要点击的位置。
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" onClick={active ? onStop : onStart}>
          {active ? "退出指引" : "开始指引"}
        </Button>
        <Button size="sm" variant="outline" onClick={onClose}>
          先不用
        </Button>
      </div>
    </div>
  );
}

function GuideContextCard({
  expanded,
  currentStep,
  onToggle,
  onGo,
  onSkip,
}: {
  expanded: boolean;
  currentStep: number;
  onToggle: () => void;
  onGo: () => void;
  onSkip: () => void;
}) {
  const step = GUIDE_STEPS[currentStep];
  const percent = ((currentStep + 1) / GUIDE_STEPS.length) * 100;

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={onToggle}
        className="absolute right-0 top-full z-30 mt-2 inline-flex items-center gap-2 rounded-full border border-primary/30 bg-card/95 px-3 py-1.5 text-xs font-medium text-primary shadow-lg backdrop-blur transition hover:bg-primary/10"
        aria-label="展开当前步骤"
      >
        <Sparkles className="h-4 w-4" />
        第 1 步：先添加账号
      </button>
    );
  }

  return (
    <div className="absolute right-0 top-full z-30 mt-2 w-[19rem] rounded-2xl border bg-card/95 p-4 text-left shadow-xl backdrop-blur">
      <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>新手指引</span>
        <button type="button" onClick={onToggle} className="hover:text-foreground">
          收起
        </button>
      </div>
      <div className="mb-2 text-sm font-semibold">{step.title}</div>
      <p className="text-xs leading-relaxed text-muted-foreground">{step.desc}</p>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-all"
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" onClick={onGo}>
          {step.actionLabel} <ArrowRight className="ml-1 h-4 w-4" />
        </Button>
        <Button size="sm" variant="outline" onClick={onSkip}>
          跳过这步
        </Button>
      </div>
    </div>
  );
}
