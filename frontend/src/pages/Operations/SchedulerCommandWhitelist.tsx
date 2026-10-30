import { useEffect, useRef, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Save, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { getEffectiveConfig, updateAccountFeatureConfig } from "@/api/features";
import type { SchedulerFeatureConfig } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import { Textarea } from "@/components/ui/textarea";
import { getErrMsg } from "@/lib/api";

/**
 * 自动指令白名单（第 20 轮第二批起并入「定时任务」分页，原独立分页 /operations/auto-command-whitelist 跳转到这里）。
 * 存在定时任务配置 scheduler.allowed_command_whitelist 里，定时任务与自动回复里「执行指令」的动作共用。
 */
export function SchedulerCommandWhitelistCard({ aid }: { aid: number }) {
  const [searchParams] = useSearchParams();
  const highlight = searchParams.get("section") === "whitelist";
  const cardRef = useRef<HTMLDivElement>(null);
  const [whitelistText, setWhitelistText] = useState("");
  const [dirty, setDirty] = useState(false);

  const schedulerCfgQ = useQuery({
    queryKey: ["features", aid, "scheduler", "config"],
    queryFn: () => getEffectiveConfig(aid, "scheduler") as Promise<SchedulerFeatureConfig>,
    enabled: aid > 0,
  });
  const schedulerWhitelist = (schedulerCfgQ.data?.allowed_command_whitelist || []).join("\n");

  useEffect(() => {
    setDirty(false);
  }, [aid]);

  useEffect(() => {
    if (!dirty) setWhitelistText(schedulerWhitelist);
  }, [schedulerWhitelist, dirty]);

  useEffect(() => {
    if (!highlight || schedulerCfgQ.isLoading) return;
    cardRef.current?.scrollIntoView({ block: "start", behavior: "auto" });
  }, [highlight, schedulerCfgQ.isLoading]);

  const saveMut = useMutation({
    mutationFn: async () => {
      const whitelist = whitelistText.split("\n").map((item) => item.trim()).filter(Boolean);
      await updateAccountFeatureConfig(aid, "scheduler", { allowed_command_whitelist: whitelist });
    },
    onSuccess: async () => {
      setDirty(false);
      await schedulerCfgQ.refetch();
      toast.success("自动指令白名单已保存");
    },
    onError: (error) => toast.error(getErrMsg(error)),
  });

  return (
    <div ref={cardRef} id="auto-command-whitelist" className="scroll-mt-4">
      <Card className={highlight ? "siri-glow-soft" : undefined} data-scheduler-whitelist>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="h-4 w-4" />
            自动指令白名单
          </CardTitle>
          <CardDescription className="space-y-1">
            <span className="block">定时任务和自动回复里「执行指令」的动作，只能执行这里列出的指令；这是账号级安全边界。</span>
            <span className="block">每行一个指令 key（不带指令前缀），例如 <code>help</code> 或自定义指令名。</span>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            aria-label="自动指令白名单"
            value={whitelistText}
            onChange={(event) => { setWhitelistText(event.target.value); setDirty(true); }}
            placeholder={"测试\nhelp"}
            rows={6}
            disabled={!aid || schedulerCfgQ.isLoading}
          />
          <div className="flex justify-end">
            <Button onClick={() => saveMut.mutate()} disabled={!aid || !dirty || saveMut.isPending || schedulerCfgQ.isLoading}>
              {saveMut.isPending ? <Spinner className="mr-2" /> : <Save className="mr-2 h-4 w-4" />}
              保存白名单
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/** 旧地址 /operations/auto-command-whitelist?aid=… → 定时任务分页的白名单区块（保留 aid）。 */
export function SchedulerWhitelistRedirect() {
  const [searchParams] = useSearchParams();
  const params = new URLSearchParams();
  const aid = searchParams.get("aid");
  if (aid) params.set("aid", aid);
  params.set("section", "whitelist");
  return <Navigate to={`/operations/scheduler?${params.toString()}`} replace />;
}
