import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Bot,
  ChevronDown,
  Download,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  UserPlus,
  Waypoints,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  getGlobalLimits,
  getSystemSettings,
  patchSystemSettings,
  putGlobalLimits,
} from "@/api/system";
import { listAccounts } from "@/api/accounts";
import { getErrMsg, api } from "@/lib/api";
import { NotifyBots } from "./NotifyBots";
import { DeviceProfileManager } from "./DeviceProfileManager";
import { ProxyManager } from "./ProxyManager";
import { RateTemplates } from "./RateTemplates";
import { SudoManagement } from "./SudoManagement";
import { UserAccount } from "./UserAccount";
import { ConfigBackup } from "./ConfigBackup";

interface KillSwitchState {
  enabled: boolean;
}

type RuntimeLogLevel = "debug" | "info" | "warn" | "error";

const GUIDE_STEPS = [
  {
    title: "1. 添加并启用账号",
    desc: "先新增 Telegram 账号并启用它，系统会为该账号启动独立 worker。",
    actionLabel: "去添加账号",
    actionTo: "/accounts/new",
  },
  {
    title: "2. 设置命令前缀",
    desc: "在系统设置里确定命令开头字符，比如 ,ai。",
    actionLabel: "去设置前缀",
    actionTo: "/settings?tab=platform",
  },
  {
    title: "3. 启用命令模板或调用模块",
    desc: "去模块中心启用模板或模块，然后就能在 Telegram 里直接调用。",
    actionLabel: "去模块中心",
    actionTo: "/plugins",
  },
];

function getGuideStepByPath(pathname: string, search: string): number {
  if (pathname === "/accounts" || pathname === "/accounts/new") return 0;
  if (pathname === "/settings" && new URLSearchParams(search).get("tab") === "platform") return 1;
  if (pathname === "/plugins" || pathname.startsWith("/plugins/")) return 2;
  return 0;
}

export function SettingsIndex() {
  const qc = useQueryClient();
  const location = useLocation();
  const nav = useNavigate();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<"account" | "platform" | "proxy-identity" | "security" | "migration">("account");
  const [rateExpanded, setRateExpanded] = useState(false);
  const [guideExpanded, setGuideExpanded] = useState(false);
  const [quickAid, setQuickAid] = useState("");
  const [quickBindOpen, setQuickBindOpen] = useState(false);
  const guideActive = searchParams.get("guide") === "1";
  const currentStep = useMemo(
    () => getGuideStepByPath(location.pathname, location.search),
    [location.pathname, location.search],
  );

  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const limitsQ = useQuery({
    queryKey: ["system", "global-limits"],
    queryFn: getGlobalLimits,
  });
  const killQ = useQuery<KillSwitchState>({
    queryKey: ["system", "kill-switch"],
    queryFn: async () => (await api.get("/api/system/kill-switch")).data,
  });
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  const [prefix, setPrefix] = useState("");
  const [timezone, setTimezone] = useState("");
  const [llmLimits, setLlmLimits] = useState({
    per_minute: "0",
    daily_requests: "0",
    daily_tokens: "0",
    premium_daily: "0",
  });
  const [logRetention, setLogRetention] = useState({
    runtime_log_retention_days: "30",
    runtime_log_max_message_chars: "2000",
    runtime_log_max_detail_chars: "8000",
    runtime_log_min_level: "info" as RuntimeLogLevel,
  });
  useEffect(() => {
    if (settingsQ.data) {
      setPrefix(settingsQ.data.command_prefix ?? ",");
      setTimezone(settingsQ.data.timezone ?? "");
      setLlmLimits({
        per_minute: String(settingsQ.data.llm_limits?.per_minute ?? 0),
        daily_requests: String(settingsQ.data.llm_limits?.daily_requests ?? 0),
        daily_tokens: String(settingsQ.data.llm_limits?.daily_tokens ?? 0),
        premium_daily: String(settingsQ.data.llm_limits?.premium_daily ?? 0),
      });
      setLogRetention({
        runtime_log_retention_days: String(settingsQ.data.log_retention?.runtime_log_retention_days ?? 30),
        runtime_log_max_message_chars: String(settingsQ.data.log_retention?.runtime_log_max_message_chars ?? 2000),
        runtime_log_max_detail_chars: String(settingsQ.data.log_retention?.runtime_log_max_detail_chars ?? 8000),
        runtime_log_min_level: (settingsQ.data.log_retention?.runtime_log_min_level ?? "info") as RuntimeLogLevel,
      });
    }
  }, [settingsQ.data]);

  useEffect(() => {
    const accounts = accountsQ.data ?? [];
    if (accounts.length === 0) {
      setQuickAid("");
      return;
    }
    if (!quickAid || !accounts.some((a) => String(a.id) === quickAid)) {
      setQuickAid(String(accounts[0].id));
    }
  }, [accountsQ.data, quickAid]);

  const [qps, setQps] = useState("0");
  useEffect(() => {
    if (limitsQ.data) setQps(String(limitsQ.data.api_qps_total ?? 0));
  }, [limitsQ.data]);

  useEffect(() => {
    const tabParam = searchParams.get("tab");
    if (tabParam === "backup") {
      setTab("migration");
      return;
    }
    if (tabParam === "proxy" || tabParam === "device" || tabParam === "resource" || tabParam === "proxy-identity") {
      setTab("proxy-identity");
      return;
    }
    if (tabParam === "rate") {
      setTab("security");
      return;
    }
    if (
      tabParam === "account" ||
      tabParam === "platform" ||
      tabParam === "security" ||
      tabParam === "migration"
    ) {
      setTab(tabParam as "account" | "platform" | "security" | "migration");
    }
  }, [searchParams]);

  useEffect(() => {
    const accounts = accountsQ.data ?? [];
    if (quickAid || accounts.length === 0) return;
    setQuickAid(String(accounts[0].id));
  }, [accountsQ.data, quickAid]);

  const savePrefix = useMutation({
    mutationFn: () => patchSystemSettings({ command_prefix: prefix }),
    onSuccess: () => {
      toast.success("命令前缀已保存（worker 将热加载）");
      qc.invalidateQueries({ queryKey: ["system", "settings"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const saveTimezone = useMutation({
    mutationFn: () => patchSystemSettings({ timezone }),
    onSuccess: () => {
      toast.success("时区已保存");
      qc.invalidateQueries({ queryKey: ["system", "settings"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const saveLlmLimits = useMutation({
    mutationFn: () => patchSystemSettings({
      llm_limits: {
        per_minute: Number(llmLimits.per_minute) || 0,
        daily_requests: Number(llmLimits.daily_requests) || 0,
        daily_tokens: Number(llmLimits.daily_tokens) || 0,
        premium_daily: Number(llmLimits.premium_daily) || 0,
      },
    }),
    onSuccess: () => {
      toast.success("LLM 限额已保存");
      qc.invalidateQueries({ queryKey: ["system", "settings"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const saveLogRetention = useMutation({
    mutationFn: () => patchSystemSettings({
      log_retention: {
        runtime_log_retention_days: Number(logRetention.runtime_log_retention_days) || 0,
        runtime_log_max_message_chars: Number(logRetention.runtime_log_max_message_chars) || 2000,
        runtime_log_max_detail_chars: Number(logRetention.runtime_log_max_detail_chars) || 0,
        runtime_log_min_level: logRetention.runtime_log_min_level,
      },
    }),
    onSuccess: () => {
      toast.success("日志保留策略已保存");
      qc.invalidateQueries({ queryKey: ["system", "settings"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const saveQps = useMutation({
    mutationFn: () => putGlobalLimits(Number(qps) || 0),
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["system", "global-limits"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const killMut = useMutation({
    mutationFn: async (next: boolean) => {
      await api.post("/api/system/kill-switch", { enabled: next });
    },
    onSuccess: () => {
      toast.success("已下发");
      qc.invalidateQueries({ queryKey: ["system", "kill-switch"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const loading = settingsQ.isLoading || limitsQ.isLoading || killQ.isLoading;
  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-24">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">系统设置</h1>
        <p className="text-sm text-muted-foreground">
          按账号、平台、安全、迁移拆分，保留常用入口并收敛历史配置位。
        </p>
      </div>

      <Card className="border-dashed">
        <CardHeader>
          <CardTitle className="text-base">猜你想要？</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <Button asChild variant="outline" size="sm">
            <Link to="/ai/providers">添加模型</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/plugins/templates">添加命令</Link>
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={(accountsQ.data ?? []).length === 0}
            onClick={() => {
              const accounts = accountsQ.data ?? [];
              if (accounts.length === 1) {
                nav(`/accounts/${accounts[0].id}?tab=bot`);
                return;
              }
              setQuickBindOpen(true);
            }}
          >
            <Bot className="mr-1 h-4 w-4" /> 绑定机器人
          </Button>
        </CardContent>
      </Card>

      <Dialog open={quickBindOpen} onOpenChange={setQuickBindOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>选择要绑定机器人的账号</DialogTitle>
            <DialogDescription>
              请选择一个账号，进入该账号的 Bot 联动配置页。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="quick-bind-account">账号</Label>
            <Select
              id="quick-bind-account"
              value={quickAid}
              onChange={(e) => setQuickAid(e.target.value)}
              className="w-full"
              disabled={(accountsQ.data ?? []).length === 0}
            >
              {(accountsQ.data ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name || (a.tg_username ? `@${a.tg_username}` : a.phone)}
                </option>
              ))}
            </Select>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setQuickBindOpen(false)}>
              取消
            </Button>
            <Button
              disabled={!quickAid}
              onClick={() => {
                setQuickBindOpen(false);
                nav(`/accounts/${quickAid}?tab=bot`);
              }}
            >
              前往配置
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList>
          <TabsTrigger value="account" className="gap-1.5">
            <ShieldCheck className="h-4 w-4" /> 账号
          </TabsTrigger>
          <TabsTrigger value="platform" className="gap-1.5">
            <SlidersHorizontal className="h-4 w-4" /> 平台
          </TabsTrigger>
          <TabsTrigger value="proxy-identity" className="gap-1.5">
            <Waypoints className="h-4 w-4" /> 代理与标识
          </TabsTrigger>
          <TabsTrigger value="security" className="gap-1.5">
            <UserPlus className="h-4 w-4" /> 安全
          </TabsTrigger>
          <TabsTrigger value="migration" className="gap-1.5">
            <Download className="h-4 w-4" /> 迁移
          </TabsTrigger>
        </TabsList>

        <TabsContent value="account" className="space-y-6">
          <UserAccount />
          <SudoManagement />
        </TabsContent>

        <TabsContent value="platform" className="space-y-6">
          <Card className={guideActive && currentStep === 1 ? "siri-glow-soft" : undefined}>
            <CardHeader>
              <CardTitle className="text-base">命令前缀</CardTitle>
              <CardDescription>
                TG 内命令开头字符（默认 <code>,</code>）。修改后 worker 自动热加载
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex max-w-xs items-end gap-2">
                <div className="flex-1 space-y-1.5">
                  <Label>前缀</Label>
                  <Input
                    value={prefix}
                    maxLength={3}
                    onChange={(e) => setPrefix(e.target.value)}
                  />
                </div>
                <Button
                  className={
                    guideActive && currentStep === 1
                      ? "siri-glow border border-primary/25 bg-background text-primary shadow-sm hover:bg-primary/10 hover:text-primary"
                      : undefined
                  }
                  onClick={() => prefix && savePrefix.mutate()}
                  disabled={savePrefix.isPending}
                >
                  保存
                </Button>
              </div>
              {guideActive ? (
                <div className="mt-3">
                  <GuideInlineCard
                    expanded={guideExpanded}
                    currentStep={currentStep}
                    onToggle={() => setGuideExpanded((v) => !v)}
                    onPrimary={() => nav("/plugins?guide=1")}
                    onSkip={() => nav("/plugins?guide=1")}
                  />
                </div>
              ) : null}
              <div className="mt-4 max-w-[460px] rounded-xl border bg-background p-3 text-xs">
                <div className="mb-3 font-medium">触发预览</div>
                <div className="rounded-2xl border bg-gradient-to-b from-sky-50 to-emerald-50 p-4 dark:from-sky-950/30 dark:to-emerald-950/20">
                  <div className="space-y-2.5">
                    <div className="w-fit max-w-[78%] rounded-2xl rounded-bl-lg border bg-card px-3.5 py-2.5 text-foreground shadow-sm sm:max-w-[66%]">
                      <div className="font-mono text-sm">
                        这是一段被回复的原文。
                      </div>
                    </div>

                    <div className="ml-auto w-fit max-w-[68%] rounded-2xl rounded-br-lg bg-sky-500 px-3.5 py-2.5 text-white shadow-sm sm:max-w-[52%]">
                      <div className="mb-1.5 inline-block max-w-full rounded-lg border-l-2 border-white/70 bg-white/15 px-2 py-1 text-[11px] leading-relaxed text-white/90">
                        这是一段被回复的原文。
                      </div>
                      <div className="font-mono text-sm">{prefix || ","}ai 请总结这段内容</div>
                    </div>

                    <div className="ml-auto w-fit max-w-[78%] rounded-2xl rounded-br-lg bg-sky-500 px-3.5 py-2.5 text-white shadow-sm sm:max-w-[66%]">
                      <div className="font-semibold text-sm">{prefix || ","}(๑•̌.•̑๑)ˀ̣ˀ̣ˀ̣ 好奇</div>
                      <div className="mt-2 inline-block max-w-full rounded-lg border-l-2 border-white/60 bg-white/15 px-2 py-1 text-white/90">
                        这是一段被回复的原文。
                      </div>
                      <div className="mt-2 block w-fit max-w-full rounded-lg border-l-2 border-white/60 bg-white/15 px-2 py-1 text-white/90">
                        请总结这段内容
                      </div>
                      <div className="mt-2.5 font-semibold text-sm">ᕦ(ˇò_ó)ᕤ 回答</div>
                      <p className="mt-2 text-white/90 leading-relaxed">
                        这是 AI 回答示例，已按当前消息模板渲染。
                      </p>
                      <div className="mt-2 inline-block max-w-full rounded-lg border-l-2 border-white/60 bg-white/15 px-2 py-1 text-white/90">
                        这里是从第三行开始的回答内容。
                      </div>
                      <div className="my-2.5 text-left text-white/70">━━━━━━━━━━━━━━━</div>
                      <div className="text-left font-semibold text-white/95 text-[11px]">✦ GPT-5.5 · OpenAI ✦</div>
                    </div>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">通知渠道</CardTitle>
              <CardDescription>设置系统事件的推送目标与通知机器人。</CardDescription>
            </CardHeader>
            <CardContent>
              <NotifyBots />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="proxy-identity" className="space-y-6">
          <ProxyManager />
          <DeviceProfileManager />
        </TabsContent>

        <TabsContent value="security" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">全局总闸（Kill Switch）</CardTitle>
              <CardDescription>
                开启后所有账号 worker 立即暂停，仅保留接收
              </CardDescription>
            </CardHeader>
            <CardContent className="flex items-center gap-4">
              <Switch
                checked={!!killQ.data?.enabled}
                onCheckedChange={(v) => {
                  if (v && !confirm("确认开启总闸？所有账号立即暂停！")) return;
                  killMut.mutate(v);
                }}
              />
              <span className="text-sm text-muted-foreground">
                当前：{killQ.data?.enabled ? "已暂停" : "正常运行"}
              </span>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">全局每秒 API 上限</CardTitle>
              <CardDescription>0 = 不限制</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex max-w-xs items-end gap-2">
                <div className="flex-1 space-y-1.5">
                  <Label>API 查询总数</Label>
                  <Input
                    inputMode="numeric"
                    value={qps}
                    onChange={(e) => setQps(e.target.value.replace(/[^0-9]/g, ""))}
                  />
                </div>
                <Button onClick={() => saveQps.mutate()} disabled={saveQps.isPending}>
                  保存
                </Button>
              </div>
            </CardContent>
          </Card>

          <div className="rounded-lg border bg-card">
            <div className="p-6">
              <button
                type="button"
                className="flex w-full items-center justify-between gap-2 text-left"
                onClick={() => setRateExpanded((v) => !v)}
              >
                <div>
                  <CardTitle className="text-base">频控模板</CardTitle>
                  <CardDescription>管理历史的速率限制模板，默认收起。</CardDescription>
                </div>
                <ChevronDown
                  className={`h-4 w-4 text-muted-foreground transition-transform ${rateExpanded ? "rotate-180" : ""}`}
                />
              </button>
            </div>
            {rateExpanded ? (
              <div className="border-t p-4">
                <RateTemplates />
              </div>
            ) : null}
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">时区设置</CardTitle>
              <CardDescription>
                全局时区，影响定时任务"下次触发/上次触发"等时间显示。留空则使用浏览器本地时区。
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex max-w-sm items-end gap-2">
                <div className="flex-1 space-y-1.5">
                  <Label>IANA 时区</Label>
                  <Input
                    value={timezone}
                    onChange={(e) => setTimezone(e.target.value)}
                    placeholder="如 Asia/Shanghai，留空为浏览器时区"
                  />
                  <p className="text-xs text-muted-foreground">
                    当前浏览器时区：<b>{Intl.DateTimeFormat().resolvedOptions().timeZone}</b>
                  </p>
                </div>
                <Button onClick={() => saveTimezone.mutate()} disabled={saveTimezone.isPending}>
                  保存
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">日志保留策略</CardTitle>
              <CardDescription>
                控制运行日志保存多久，以及单条日志内容最多保存多少字符。0 天表示不自动删除。
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-3 md:grid-cols-4">
                <div className="space-y-1.5">
                  <Label>保留天数</Label>
                  <Input
                    inputMode="numeric"
                    value={logRetention.runtime_log_retention_days}
                    onChange={(e) =>
                      setLogRetention((v) => ({
                        ...v,
                        runtime_log_retention_days: e.target.value.replace(/[^0-9]/g, ""),
                      }))
                    }
                  />
                  <p className="text-xs text-muted-foreground">默认 30；0 = 不自动删除</p>
                </div>
                <div className="space-y-1.5">
                  <Label>消息正文最多字符</Label>
                  <Input
                    inputMode="numeric"
                    value={logRetention.runtime_log_max_message_chars}
                    onChange={(e) =>
                      setLogRetention((v) => ({
                        ...v,
                        runtime_log_max_message_chars: e.target.value.replace(/[^0-9]/g, ""),
                      }))
                    }
                  />
                  <p className="text-xs text-muted-foreground">默认 2000，最小 200</p>
                </div>
                <div className="space-y-1.5">
                  <Label>结构化详情最多字符</Label>
                  <Input
                    inputMode="numeric"
                    value={logRetention.runtime_log_max_detail_chars}
                    onChange={(e) =>
                      setLogRetention((v) => ({
                        ...v,
                        runtime_log_max_detail_chars: e.target.value.replace(/[^0-9]/g, ""),
                      }))
                    }
                  />
                  <p className="text-xs text-muted-foreground">默认 8000；0 = 不保存 detail</p>
                </div>
                <div className="space-y-1.5">
                  <Label>最小日志级别</Label>
                  <Select
                    value={logRetention.runtime_log_min_level}
                    onChange={(e) =>
                      setLogRetention((v) => ({
                        ...v,
                        runtime_log_min_level: e.target.value as RuntimeLogLevel,
                      }))
                    }
                  >
                    <option value="debug">debug（排障最详细）</option>
                    <option value="info">info（默认）</option>
                    <option value="warn">warn（只看告警和错误）</option>
                    <option value="error">error（只看错误）</option>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    在主进程落库前过滤低级别日志，修改后对新日志生效。
                  </p>
                </div>
              </div>
              <div className="mt-3">
                <Button onClick={() => saveLogRetention.mutate()} disabled={saveLogRetention.isPending}>
                  保存
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">LLM 成本限额</CardTitle>
              <CardDescription>0 = 不限制；按账号统计，worker 调用前生效</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-3 md:grid-cols-4">
                <div className="space-y-1.5">
                  <Label>每分钟调用</Label>
                  <Input
                    inputMode="numeric"
                    value={llmLimits.per_minute}
                    onChange={(e) => setLlmLimits((v) => ({ ...v, per_minute: e.target.value.replace(/[^0-9]/g, "") }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>每日调用</Label>
                  <Input
                    inputMode="numeric"
                    value={llmLimits.daily_requests}
                    onChange={(e) => setLlmLimits((v) => ({ ...v, daily_requests: e.target.value.replace(/[^0-9]/g, "") }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>每日 Token</Label>
                  <Input
                    inputMode="numeric"
                    value={llmLimits.daily_tokens}
                    onChange={(e) => setLlmLimits((v) => ({ ...v, daily_tokens: e.target.value.replace(/[^0-9]/g, "") }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>高价每日调用</Label>
                  <Input
                    inputMode="numeric"
                    value={llmLimits.premium_daily}
                    onChange={(e) => setLlmLimits((v) => ({ ...v, premium_daily: e.target.value.replace(/[^0-9]/g, "") }))}
                  />
                </div>
              </div>
              <div className="mt-3">
                <Button onClick={() => saveLlmLimits.mutate()} disabled={saveLlmLimits.isPending}>
                  保存
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="migration">
          <ConfigBackup />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function GuideInlineCard({
  expanded,
  currentStep,
  onToggle,
  onPrimary,
  onSkip,
}: {
  expanded: boolean;
  currentStep: number;
  onToggle: () => void;
  onPrimary: () => void;
  onSkip: () => void;
}) {
  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const cmdPrefix = settingsQ.data?.command_prefix || ",";
  const step = {
    ...GUIDE_STEPS[currentStep],
    desc:
      currentStep === 1
        ? `在系统设置里确定命令开头字符，比如 ${cmdPrefix}ai。`
        : GUIDE_STEPS[currentStep].desc,
  };
  const percent = ((currentStep + 1) / GUIDE_STEPS.length) * 100;

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={onToggle}
        className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary shadow-sm shadow-primary/20 transition hover:bg-primary/15"
        aria-label="打开新手指引"
      >
        <Sparkles className="h-4 w-4" />
        新手指引：当前第 2 步，点击展开详情
      </button>
    );
  }

  return (
    <div className="max-w-md rounded-2xl border bg-card/95 p-4 shadow-lg shadow-primary/10">
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
        <Button size="sm" onClick={onPrimary}>
          下一步：去模块中心 <ArrowRight className="ml-1 h-4 w-4" />
        </Button>
        <Button size="sm" variant="outline" onClick={onSkip}>
          跳过这步
        </Button>
      </div>
    </div>
  );
}
