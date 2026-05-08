import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { listAccountFeatures } from "@/api/accounts";
import { getSystemSettings } from "@/api/system";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/misc";
import { getErrMsg } from "@/lib/api";

interface Game24Config {
  command: string;
  timeout: number;
}

function clampInt(s: string, min: number, max: number): number {
  const cleaned = s.replace(/[^0-9]/g, "");
  if (!cleaned) return min;
  const n = parseInt(cleaned, 10);
  return Math.max(min, Math.min(max, Number.isNaN(n) ? min : n));
}

const DEFAULT_CONFIG: Game24Config = {
  command: "24d",
  timeout: 500,
};

export function Game24ConfigPage() {
  const params = useParams();
  const aid = Number(params.aid);
  const nav = useNavigate();
  const qc = useQueryClient();

  const featuresQ = useQuery({
    queryKey: ["account", aid, "features"],
    queryFn: () => listAccountFeatures(aid),
    enabled: !!aid,
  });

  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const cmdPrefix = settingsQ.data?.command_prefix || ",";

  const game24Feature = featuresQ.data?.find((f) => f.feature_key === "game24");
  const currentConfig = (game24Feature?.config ?? {}) as Partial<Game24Config>;

  const [command, setCommand] = useState(DEFAULT_CONFIG.command);
  const [timeout, setTimeout_] = useState(DEFAULT_CONFIG.timeout);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (currentConfig.command !== undefined) {
      setCommand(currentConfig.command);
    }
    if (currentConfig.timeout !== undefined) {
      setTimeout_(currentConfig.timeout);
    }
    setDirty(false);
  }, [game24Feature?.config]);

  const saveMut = useMutation({
    mutationFn: async (config: Game24Config) => {
      const { api } = await import("@/lib/api");
      await api.patch(`/api/accounts/${aid}/features/game24`, {
        enabled: true,
        config,
      });
    },
    onSuccess: () => {
      toast.success("配置已保存（worker 热加载）");
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["account", aid, "features"] });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  function handleSave() {
    saveMut.mutate({ command, timeout });
  }

  if (!aid) return <p>账号 ID 不合法</p>;
  if (featuresQ.isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => nav(`/accounts/${aid}?tab=features`)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回账号
        </Button>
        <h1 className="text-2xl font-semibold tracking-tight">24 点游戏</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">24 点游戏配置</CardTitle>
          <CardDescription>
            配置触发指令名和答题限时。修改后 worker 会自动热加载，无需重启。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6 max-w-lg">
          {/* 指令名 */}
          <div className="space-y-1.5">
            <Label htmlFor="command">触发指令名</Label>
            <p className="text-xs text-muted-foreground">
              在系统命令前缀后输入此指令名触发游戏。默认
              <code className="mx-1">24d</code>
              ，即发送 <code>{cmdPrefix}{command} 奖金金额</code> 开始游戏。
            </p>
            <Input
              id="command"
              className="font-mono w-40"
              value={command}
              onChange={(e) => {
                setCommand(e.target.value.trim());
                setDirty(true);
              }}
            />
          </div>

          {/* 超时 */}
          <div className="space-y-1.5">
            <Label htmlFor="timeout">答题限时（秒）</Label>
            <p className="text-xs text-muted-foreground">
              超过此时间无人答对，游戏自动结束。默认 500 秒（约 8 分钟）。
            </p>
            <Input
              id="timeout"
              inputMode="numeric"
              className="w-32"
              value={String(timeout)}
              onChange={(e) => {
                setTimeout_(clampInt(e.target.value, 30, 3600));
                setDirty(true);
              }}
            />
          </div>

          {/* 状态 */}
          {game24Feature && (
            <div className="rounded-md border bg-muted/30 p-3 text-xs">
              <div className="font-medium">当前状态</div>
              <div className="mt-1 text-muted-foreground">
                启用：{game24Feature.enabled ? "是" : "否"} ·
                状态：{game24Feature.state}
                {game24Feature.last_error
                  ? ` · 最近错误：${game24Feature.last_error}`
                  : ""}
              </div>
            </div>
          )}

          {/* 使用说明 */}
          <div className="rounded-md border bg-muted/20 p-3 text-xs text-muted-foreground">
            <div className="font-medium text-foreground">使用说明</div>
            <ul className="mt-1.5 list-inside list-disc space-y-0.5">
              <li>在群内发送 <code>{cmdPrefix}{command} 奖金金额</code> 开始游戏（例：{cmdPrefix}{command} 2000）</li>
              <li>系统生成 4 个数字，第一个用算式答对的人获得奖金</li>
              <li>可用运算符：+ - * / ( )，也支持 x / ÷ / × 别名</li>
              <li>指令前缀跟随系统设置，可在系统配置中修改</li>
            </ul>
          </div>

          <div className="flex items-center gap-3 pt-2">
            <Button
              disabled={!dirty || saveMut.isPending}
              onClick={handleSave}
            >
              {saveMut.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              保存
            </Button>
            {dirty && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  if (currentConfig.command !== undefined) {
                    setCommand(currentConfig.command);
                  }
                  if (currentConfig.timeout !== undefined) {
                    setTimeout_(currentConfig.timeout);
                  }
                  setDirty(false);
                }}
              >
                撤销
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
