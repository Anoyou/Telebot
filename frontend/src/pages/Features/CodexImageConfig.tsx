// Codex 图片生成配置：按账号管理 access_token / model / max_wait
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2, Eye, EyeOff } from "lucide-react";
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

interface CodexImageConfig {
  access_token: string;
  model: string;
  max_wait_seconds: number;
}

function clampInt(s: string, min: number, max: number): number {
  const cleaned = s.replace(/[^0-9]/g, "");
  if (!cleaned) return min;
  const n = parseInt(cleaned, 10);
  return Math.max(min, Math.min(max, Number.isNaN(n) ? min : n));
}

const DEFAULT_CONFIG: CodexImageConfig = {
  access_token: "",
  model: "gpt-5.4",
  max_wait_seconds: 600,
};

export function CodexImageConfigPage() {
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

  const feature = featuresQ.data?.find(
    (f) => f.feature_key === "codex_image"
  );
  const currentConfig = (feature?.config ?? {}) as Partial<CodexImageConfig>;

  const [accessToken, setAccessToken] = useState(DEFAULT_CONFIG.access_token);
  const [model, setModel] = useState(DEFAULT_CONFIG.model);
  const [maxWait, setMaxWait] = useState(DEFAULT_CONFIG.max_wait_seconds);
  const [dirty, setDirty] = useState(false);
  const [showToken, setShowToken] = useState(false);

  useEffect(() => {
    if (currentConfig.access_token !== undefined) {
      setAccessToken(currentConfig.access_token);
    }
    if (currentConfig.model !== undefined) {
      setModel(currentConfig.model);
    }
    if (currentConfig.max_wait_seconds !== undefined) {
      setMaxWait(currentConfig.max_wait_seconds);
    }
    setDirty(false);
  }, [feature?.config]);

  const saveMut = useMutation({
    mutationFn: async (config: CodexImageConfig) => {
      const { api } = await import("@/lib/api");
      await api.patch(`/api/accounts/${aid}/features/codex_image`, {
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
    saveMut.mutate({ access_token: accessToken, model, max_wait_seconds: maxWait });
  }

  function maskToken(token: string): string {
    if (!token) return "(未配置)";
    if (token.length <= 10) return `${token.slice(0, 2)}***${token.slice(-2)}`;
    return `${token.slice(0, 4)}***${token.slice(-4)}`;
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
        <Button
          variant="ghost"
          size="sm"
          onClick={() => nav(`/accounts/${aid}?tab=features`)}
        >
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回账号
        </Button>
        <h1 className="text-2xl font-semibold tracking-tight">
          Codex 图片生成
        </h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Codex 图片生成配置</CardTitle>
          <CardDescription>
            配置 Codex API 的鉴权 Token、模型和超时时间。修改后 worker
            会自动热加载，无需重启。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6 max-w-lg">
          {/* Access Token */}
          <div className="space-y-1.5">
            <Label htmlFor="access-token">Codex Access Token</Label>
            <p className="text-xs text-muted-foreground">
              从{" "}
              <code className="mx-0.5">.codex/auth.json</code>{" "}
              中获取的 access token，用于鉴权 Codex API。
            </p>
            <div className="flex gap-2">
              <Input
                id="access-token"
                className="font-mono flex-1"
                type={showToken ? "text" : "password"}
                placeholder="eyJhbGciOi..."
                value={accessToken}
                onChange={(e) => {
                  setAccessToken(e.target.value);
                  setDirty(true);
                }}
              />
              <Button
                variant="outline"
                size="icon"
                onClick={() => setShowToken(!showToken)}
                title={showToken ? "隐藏 Token" : "显示 Token"}
              >
                {showToken ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </Button>
            </div>
            {!showToken && accessToken && (
              <p className="text-xs text-muted-foreground">
                当前：{maskToken(accessToken)}
              </p>
            )}
          </div>

          {/* Model */}
          <div className="space-y-1.5">
            <Label htmlFor="model">模型名称</Label>
            <p className="text-xs text-muted-foreground">
              Codex 使用的模型，默认 <code className="mx-0.5">gpt-5.4</code>。
            </p>
            <Input
              id="model"
              className="font-mono w-48"
              value={model}
              onChange={(e) => {
                setModel(e.target.value.trim());
                setDirty(true);
              }}
            />
          </div>

          {/* Max Wait */}
          <div className="space-y-1.5">
            <Label htmlFor="max-wait">最大等待时间（秒）</Label>
            <p className="text-xs text-muted-foreground">
              图片生成最大等待时间，超时后自动停止。默认 600（10 分钟）。
            </p>
            <Input
              id="max-wait"
              inputMode="numeric"
              className="w-32"
              value={String(maxWait)}
              onChange={(e) => {
                setMaxWait(clampInt(e.target.value, 60, 1800));
                setDirty(true);
              }}
            />
          </div>

          {/* 状态 */}
          {feature && (
            <div className="rounded-md border bg-muted/30 p-3 text-xs">
              <div className="font-medium">当前状态</div>
              <div className="mt-1 text-muted-foreground">
                启用：{feature.enabled ? "是" : "否"} ·
                状态：{feature.state}
                {feature.last_error
                  ? ` · 最近错误：${feature.last_error}`
                  : ""}
              </div>
            </div>
          )}

          {/* 使用说明 */}
          <div className="rounded-md border bg-muted/20 p-3 text-xs text-muted-foreground">
            <div className="font-medium text-foreground">使用说明</div>
            <ul className="mt-1.5 list-inside list-disc space-y-0.5">
              <li>
                发送 <code>{cmdPrefix}cximg 提示词</code> 纯文本生成图片
              </li>
              <li>
                回复图片后发送{" "}
                <code>{cmdPrefix}cximg 提示词</code> 进行参考图生成
              </li>
              <li>
                也可通过命令{" "}
                <code>
                  {cmdPrefix}cximg token 你的access_token
                </code>{" "}
                直接设置 Token
              </li>
              <li>
                Token 通常在 <code>.codex/auth.json</code> 文件中获取
              </li>
            </ul>
          </div>

          <div className="flex items-center gap-3 pt-2">
            <Button disabled={!dirty || saveMut.isPending} onClick={handleSave}>
              {saveMut.isPending && (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              )}
              保存
            </Button>
            {dirty && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  if (currentConfig.access_token !== undefined) {
                    setAccessToken(currentConfig.access_token);
                  }
                  if (currentConfig.model !== undefined) {
                    setModel(currentConfig.model);
                  }
                  if (currentConfig.max_wait_seconds !== undefined) {
                    setMaxWait(currentConfig.max_wait_seconds);
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
