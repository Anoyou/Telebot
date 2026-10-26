// 一键体检：与 iOS 概览右上角入口、助手“体检一下”同一个只读检查（GET /api/system-agent/checkup）。
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CircleHelp,
  OctagonX,
  RefreshCw,
  ShieldCheck,
  Stethoscope,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/misc";
import { runSystemCheckup, type SystemCheckup, type SystemCheckupStep } from "@/api/systemAgent";
import { cn } from "@/lib/utils";

const STATUS: Record<string, { label: string; icon: typeof CheckCircle2; className: string }> = {
  ok: { label: "正常", icon: CheckCircle2, className: "text-emerald-600 dark:text-emerald-400" },
  warn: { label: "需关注", icon: AlertTriangle, className: "text-amber-600 dark:text-amber-400" },
  error: { label: "异常", icon: OctagonX, className: "text-red-600 dark:text-red-400" },
  unknown: { label: "未能检查", icon: CircleHelp, className: "text-muted-foreground" },
};

function statusOf(raw: string | null | undefined) {
  return STATUS[raw ?? ""] ?? STATUS.unknown;
}

/** 检查项／助手工具 → Web 管理台中的处理入口，只映射真实存在的页面。 */
export function checkupTarget(step: Pick<SystemCheckupStep, "tool" | "section">): { label: string; to: string } | null {
  const domain = step.tool?.split(".")[0] ?? step.section ?? "";
  switch (domain) {
    case "accounts":
    case "humanize":
    case "rate_limits":
    case "dispatch":
    case "features":
      return { label: "打开账号", to: "/accounts" };
    case "plugins":
      return { label: "打开插件", to: "/plugins" };
    case "providers":
    case "usage":
    case "routing":
      return { label: "打开 AI", to: "/ai" };
    case "logs":
    case "errors":
      return { label: "打开日志", to: "/logs" };
    case "scheduler":
      return { label: "打开定时任务", to: "/plugins/scheduler" };
    case "system_agent":
      return { label: "打开助手", to: "/assistant" };
    case "safety":
    case "notifications":
    case "system":
    case "versions":
    case "capabilities":
    case "health":
      return { label: "打开设置", to: "/settings" };
    default:
      return null;
  }
}

function StepRow({ step, onNavigate }: { step: SystemCheckupStep; onNavigate: () => void }) {
  const status = statusOf(step.severity === "info" ? "unknown" : step.severity);
  const Icon = step.severity === "info" ? CircleHelp : status.icon;
  const target = checkupTarget(step);
  return (
    <li className="flex items-start gap-2 text-sm">
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", status.className)} aria-hidden />
      <div className="min-w-0 flex-1 space-y-1">
        <p className="break-words">{step.text}</p>
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          {step.requires_confirmation ? (
            <span className="inline-flex items-center gap-1">
              <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
              需确认
            </span>
          ) : null}
          {target ? (
            <Link to={target.to} onClick={onNavigate} className="inline-flex items-center gap-1 text-primary hover:underline">
              {target.label}
              <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function CheckupResult({ result, onNavigate }: { result: SystemCheckup; onNavigate: () => void }) {
  const verdict = statusOf(result.verdict);
  const VerdictIcon = verdict.icon;
  const checkedAt = new Date(result.checked_at);
  return (
    <div className="space-y-4" data-testid="checkup-result">
      <div className="rounded-xl border border-border p-3">
        <p className={cn("flex items-center gap-2 text-base font-semibold", verdict.className)}>
          <VerdictIcon className="h-5 w-5" aria-hidden />
          {result.verdict_text}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          检查时间 {Number.isNaN(checkedAt.getTime()) ? result.checked_at : checkedAt.toLocaleTimeString()} · 统计最近{" "}
          {result.window_hours} 小时 · 用时 {(result.elapsed_ms ?? 0) / 1000} 秒
        </p>
      </div>
      {result.next_steps && result.next_steps.length > 0 ? (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">建议下一步</h3>
          <ul className="space-y-2">
            {result.next_steps.map((step, index) => (
              <StepRow key={`${step.section ?? "-"}-${index}`} step={step} onNavigate={onNavigate} />
            ))}
          </ul>
        </section>
      ) : null}
      <section className="space-y-2">
        <h3 className="text-sm font-semibold">各项检查</h3>
        <ul className="divide-y divide-border rounded-xl border border-border">
          {(result.sections ?? []).map((section) => {
            const status = statusOf(section.status);
            const Icon = status.icon;
            return (
              <li key={section.key} className="space-y-1 p-3" data-testid={`checkup-section-${section.key}`}>
                <div className="flex items-center gap-2 text-sm font-medium">
                  <Icon className={cn("h-4 w-4 shrink-0", status.className)} aria-hidden />
                  <span className="min-w-0 flex-1">{section.title}</span>
                  <span className={cn("text-xs", status.className)}>{status.label}</span>
                </div>
                <p className="break-words pl-6 text-xs text-muted-foreground">{section.finding}</p>
                {section.reason ? <p className="pl-6 text-xs text-muted-foreground">原因：{section.reason}</p> : null}
                {section.next_steps && section.next_steps.length > 0 ? (
                  <ul className="space-y-1 pl-6 pt-1">
                    {section.next_steps.map((step, index) => (
                      <StepRow key={index} step={{ ...step, section: step.section ?? section.key }} onNavigate={onNavigate} />
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
        <p className="text-xs text-muted-foreground">{result.note ?? "只读体检，未修改任何配置、未调用上游模型。"}</p>
      </section>
    </div>
  );
}

export function SystemCheckupDialog() {
  const [open, setOpen] = useState(false);
  const checkup = useMutation({ mutationFn: () => runSystemCheckup() });

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (next && !checkup.isPending && !checkup.data) checkup.mutate();
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" data-testid="dashboard-checkup">
          <Stethoscope className="mr-2 h-4 w-4 text-primary" />
          一键体检
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Stethoscope className="h-5 w-5 text-primary" aria-hidden />
            一键体检
          </DialogTitle>
          <DialogDescription>
            顺序检查基础设施、版本、总闸、账号与 Worker、错误日志、模型供应商、系统助手、定时任务、插件、通知与推送，最长约 25 秒。只读，不会修改任何配置。
          </DialogDescription>
        </DialogHeader>
        {checkup.isPending ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Spinner />
            正在体检…
          </div>
        ) : null}
        {checkup.isError ? (
          <p className="rounded-xl border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-600 dark:text-red-400">
            体检未能完成：{checkup.error instanceof Error ? checkup.error.message : "请稍后重试"}
          </p>
        ) : null}
        {checkup.data && !checkup.isPending ? <CheckupResult result={checkup.data} onNavigate={() => setOpen(false)} /> : null}
        <div className="flex justify-end">
          <Button variant="outline" size="sm" disabled={checkup.isPending} onClick={() => checkup.mutate()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            重新体检
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
