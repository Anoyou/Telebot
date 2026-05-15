// 账号列表：卡片网格形式（移动端单列），含启停 / 详情 / 删除（二次确认）操作
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  HelpCircle,
  Package,
  Plus,
  Power,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/misc";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { AccountSummaryCard } from "@/components/AccountSummaryCard";
import {
  deleteAccount,
  listAccounts,
  pauseAccount,
  resumeAccount,
} from "@/api/accounts";
import { getErrMsg } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";

const NEW_ACCOUNT_GUIDE_SEEN_KEY = "telebot.accounts.new_account_guide_seen.v2";

export function AccountList() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [guideOpen, setGuideOpen] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (localStorage.getItem(NEW_ACCOUNT_GUIDE_SEEN_KEY) === "1") return;
    setGuideOpen(true);
    localStorage.setItem(NEW_ACCOUNT_GUIDE_SEEN_KEY, "1");
  }, []);

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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">账号管理</h1>
          <p className="text-sm text-muted-foreground">
            每个账号 = 一个 session = 一个独立 worker 进程
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setGuideOpen(true)}>
            <HelpCircle className="mr-1 h-4 w-4" /> 新手指引
          </Button>
          <Button onClick={() => nav("/accounts/new")}>
            <Plus className="mr-1 h-4 w-4" /> 新增账号
          </Button>
        </div>
      </div>

      <NewAccountGuideDialog
        open={guideOpen}
        onOpenChange={setGuideOpen}
        onRunStep={(step) => {
          setGuideOpen(false);
          if (step === 0) {
            nav("/accounts/new");
            return;
          }
          nav("/plugins");
        }}
      />

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

function NewAccountGuideDialog({
  open,
  onOpenChange,
  onRunStep,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRunStep: (step: number) => void;
}) {
  const [currentStep, setCurrentStep] = useState(0);
  const steps = [
    {
      icon: Plus,
      title: "1. 添加并启用账号",
      desc: "先新增 Telegram 账号并启用它，系统会为该账号启动独立 worker。",
      actionLabel: "去添加账号",
    },
    {
      icon: Package,
      title: "2. 启用命令模板或调用插件",
      desc: "去插件中心复用命令模板，或开启需要的插件；远程插件可在“安装插件”里添加。真正发命令前，先确认系统里的命令前缀。",
      actionLabel: "去插件中心",
    },
  ];
  const step = steps[currentStep];
  const percent = ((currentStep + 1) / steps.length) * 100;

  useEffect(() => {
    if (open) setCurrentStep(0);
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>新账号怎么开始？</DialogTitle>
          <DialogDescription>
            一次做一步，做完就点下一步，不用一次记住全部流程。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 rounded-2xl border bg-gradient-to-br from-sky-50 via-background to-emerald-50 p-4 dark:from-sky-950/30 dark:via-background dark:to-emerald-950/20">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              第 {currentStep + 1} 步 / 共 {steps.length} 步
            </span>
            <span>{Math.round(percent)}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all duration-300"
              style={{ width: `${percent}%` }}
            />
          </div>
          <div className="rounded-xl border bg-card/90 p-4 shadow-sm animate-page-enter">
            <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm">
              <step.icon className="h-5 w-5" />
            </div>
            <div className="text-sm font-semibold">{step.title}</div>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {step.desc}
            </p>
            {currentStep === 1 ? (
              <Button
                asChild
                variant="outline"
                size="sm"
                className="mt-3"
                onClick={() => onOpenChange(false)}
              >
                <Link to="/settings?tab=platform">设置命令前缀</Link>
              </Button>
            ) : null}
          </div>
        </div>

        <DialogFooter className="gap-2 sm:justify-between">
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() => setCurrentStep((s) => Math.max(0, s - 1))}
              disabled={currentStep === 0}
            >
              <ArrowLeft className="mr-1 h-4 w-4" /> 上一步
            </Button>
            <Button
              variant="outline"
              onClick={() =>
                setCurrentStep((s) => Math.min(steps.length - 1, s + 1))
              }
              disabled={currentStep === steps.length - 1}
            >
              下一步 <ArrowRight className="ml-1 h-4 w-4" />
            </Button>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              先看看
            </Button>
          </div>
          <Button onClick={() => onRunStep(currentStep)}>
            {step.actionLabel} <ArrowRight className="ml-1 h-4 w-4" />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
