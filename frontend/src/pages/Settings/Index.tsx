import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Download, ShieldCheck, SlidersHorizontal, UserPlus } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
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
import { getErrMsg, api } from "@/lib/api";
import { NotifyBots } from "./NotifyBots";
import { SudoManagement } from "./SudoManagement";
import { UserAccount } from "./UserAccount";
import { ConfigBackup } from "./ConfigBackup";

interface KillSwitchState {
  enabled: boolean;
}

type RuntimeLogLevel = "debug" | "info" | "warn" | "error";

export function SettingsIndex() {
  const qc = useQueryClient();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<"account" | "platform" | "security" | "migration">("account");

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
    if (tabParam === "account" || tabParam === "platform" || tabParam === "security" || tabParam === "migration") {
      setTab(tabParam);
    }
  }, [searchParams]);

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
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">系统设置</h1>
        <p className="text-sm text-muted-foreground">
          按账号、平台、安全、迁移拆分，保留常用入口并收敛历史配置位。
        </p>
      </div>

      <Card className="border-dashed">
        <CardHeader>
          <CardTitle className="text-base">已搬家</CardTitle>
          <CardDescription>
            LLM Providers、命令模板、别名管理已迁移到新位置，旧入口已隐藏。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to="/ai/providers">前往 AI Providers</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/plugins/templates">前往命令模板</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/plugins/aliases">前往别名管理</Link>
          </Button>
        </CardContent>
      </Card>

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList>
          <TabsTrigger value="account" className="gap-1.5">
            <ShieldCheck className="h-4 w-4" /> 账号
          </TabsTrigger>
          <TabsTrigger value="platform" className="gap-1.5">
            <SlidersHorizontal className="h-4 w-4" /> 平台
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
          <Card>
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
                  onClick={() => prefix && savePrefix.mutate()}
                  disabled={savePrefix.isPending}
                >
                  保存
                </Button>
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
