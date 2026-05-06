import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { UserAccount } from "./UserAccount";

interface KillSwitchState {
  enabled: boolean;
}

export function SettingsIndex() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"global" | "security" | "notify">("global");

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
  useEffect(() => {
    if (settingsQ.data) setPrefix(settingsQ.data.command_prefix ?? ",");
  }, [settingsQ.data]);

  const [qps, setQps] = useState("0");
  useEffect(() => {
    if (limitsQ.data) setQps(String(limitsQ.data.api_qps_total ?? 0));
  }, [limitsQ.data]);

  const savePrefix = useMutation({
    mutationFn: () => patchSystemSettings({ command_prefix: prefix }),
    onSuccess: () => {
      toast.success("命令前缀已保存（worker 将热加载）");
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
          按用途拆分为全局控制、管理员账号、通知渠道，减少跨页面跳转。
        </p>
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList>
          <TabsTrigger value="global" className="gap-1.5">
            <SlidersHorizontal className="h-4 w-4" /> 全局控制
          </TabsTrigger>
          <TabsTrigger value="security" className="gap-1.5">
            <ShieldCheck className="h-4 w-4" /> 管理员账号
          </TabsTrigger>
          <TabsTrigger value="notify" className="gap-1.5">
            <Bell className="h-4 w-4" /> 通知渠道
          </TabsTrigger>
        </TabsList>

        <TabsContent value="global" className="space-y-6">
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
        </TabsContent>

        <TabsContent value="security">
          <UserAccount />
        </TabsContent>

        <TabsContent value="notify">
          <NotifyBots />
        </TabsContent>
      </Tabs>
    </div>
  );
}
