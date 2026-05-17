import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  FlaskConical,
  Package2,
  SatelliteDish,
  Settings2,
  Sparkles,
} from "lucide-react";

import {
  getFeatureMatrix,
  getPluginGlobalConfig,
  setPluginGlobalConfig,
  updateAccountFeatureConfig,
} from "@/api/features";
import { getSystemSettings } from "@/api/system";
import { listAccountFeatures } from "@/api/accounts";
import type { FeatureInfo } from "@/api/types";
import { ConfigDialog, type ConfigSchema } from "@/components/plugin/ConfigDialog";
import { Spinner } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { isPlatformFeature } from "@/lib/plugin-modes";

import { featureConfigPath } from "./_shared/featureConfig";

type Zone = "platform" | "builtin" | "remote" | "experimental";
const DANGEROUS_CMD_BANNER_KEY = "telebot.plugins_home.banner.v0_13_dangerous_cmds_closed";

export function PluginsHome() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [searchParams] = useSearchParams();
  const [selectedAid, setSelectedAid] = useState<number | null>(null);
  const [guideExpanded, setGuideExpanded] = useState(false);
  const [configDialog, setConfigDialog] = useState<{
    key: string;
    name: string;
    schema: Record<string, unknown> | null;
    globalConfig: Record<string, unknown>;
    accountConfig: Record<string, unknown>;
  } | null>(null);
  const guideActive = searchParams.get("guide") === "1";
  const [bannerVisible, setBannerVisible] = useState(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem(DANGEROUS_CMD_BANNER_KEY) !== "1";
  });
  const matrixQ = useQuery({
    queryKey: ["matrix"],
    queryFn: getFeatureMatrix,
  });
  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });

  const accounts = matrixQ.data?.accounts ?? [];
  const features = matrixQ.data?.features ?? [];
  const accountFeaturesQ = useQuery({
    queryKey: ["account", selectedAid, "features"],
    queryFn: () => listAccountFeatures(selectedAid as number),
    enabled: selectedAid != null,
  });

  useEffect(() => {
    if (accounts.length === 0) return;

    const accountParam = searchParams.get("account");
    const requestedAid = accountParam ? Number(accountParam) : NaN;
    const validRequestedAid =
      Number.isInteger(requestedAid) && accounts.some((a) => a.id === requestedAid);

    if (validRequestedAid) {
      setSelectedAid(requestedAid);
      return;
    }

    setSelectedAid((prev) => {
      if (prev !== null && accounts.some((a) => a.id === prev)) return prev;
      return accounts[0].id;
    });
  }, [accounts, searchParams]);

  const selectedAccount = accounts.find((a) => a.id === selectedAid) ?? null;
  const codexImageFeature = features.find((f) => f.key === "codex_image");
  const codexImageState = selectedAccount?.features?.codex_image ?? "disabled";
  const cmdPrefix = settingsQ.data?.command_prefix || ",";

  const grouped = useMemo(() => {
    const zones: Record<Zone, typeof features> = {
      platform: [],
      builtin: [],
      remote: [],
      experimental: [],
    };

    for (const feature of features) {
      const forceExperimental = feature.key === "codex_image";
      if (forceExperimental || feature.experimental) {
        zones.experimental.push(feature);
      } else if (isPlatformFeature(feature)) {
        zones.platform.push(feature);
      } else if (feature.is_builtin) {
        zones.builtin.push(feature);
      } else {
        zones.remote.push(feature);
      }
    }

    return zones;
  }, [features]);

  async function openGenericConfig(feature: FeatureInfo) {
    if (!selectedAccount) return;
    const accountFeatureData =
      accountFeaturesQ.data ?? (await accountFeaturesQ.refetch()).data ?? [];
    const item = accountFeatureData.find((x) => x.feature_key === feature.key);
    let globalConfig: Record<string, unknown> = {};
    try {
      globalConfig = await getPluginGlobalConfig(feature.key);
    } catch {
      globalConfig = {};
    }
    setConfigDialog({
      key: feature.key,
      name: feature.display_name,
      schema: feature.config_schema ?? null,
      globalConfig,
      accountConfig: item?.config ?? {},
    });
  }

  if (matrixQ.isLoading) {
    return (
      <div className="flex h-[40vh] items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-24">
      {bannerVisible ? (
        <Card className="border-amber-300 bg-amber-50">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">0.13 安全变更提醒</CardTitle>
            <CardDescription className="text-amber-900/90">
              Telegram 内高危命令（如 <code>{cmdPrefix}reboot</code>、<code>{cmdPrefix}plugin install</code>）已移除，请改为在 Web 控制台或账号 Bot 内执行。
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => nav(selectedAid ? `/accounts/${selectedAid}?tab=bot` : "/accounts")}
            >
              前往账号 Bot
            </Button>
            <Button size="sm" variant="outline" onClick={() => nav("/plugins/manage?tab=plugins")}>
              前往模块安装
            </Button>
            <Button
              size="sm"
              onClick={() => {
                localStorage.setItem(DANGEROUS_CMD_BANNER_KEY, "1");
                setBannerVisible(false);
              }}
            >
              我知道了，不再提示
            </Button>
          </CardContent>
        </Card>
      ) : null}
      <Card>
        <CardHeader>
          <CardTitle>模块中心</CardTitle>
          <CardDescription>
            先在这里沉淀一套好用的命令、消息和 AI 模板，再按账号启用复用；新账号不用从零重配。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
            <Button
              variant="outline"
              className={`h-full min-h-[96px] justify-start whitespace-normal px-4 py-3 text-left ${
                guideActive ? "siri-glow" : ""
              }`}
              onClick={() => nav("/plugins/templates")}
            >
              <span>
                <span className="block font-medium">命令模板</span>
                <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                  先把常用回复、转发、AI 命令整理成一套模板，再按账号开启复用。
                </span>
              </span>
            </Button>
            <Button
              variant="outline"
              className="h-full min-h-[96px] justify-start whitespace-normal px-4 py-3 text-left"
              onClick={() => nav("/plugins/aliases")}
            >
              <span>
                <span className="block font-medium">命令别名</span>
                <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                  给常用命令起短名字，减少不同账号之间重复记命令。
                </span>
              </span>
            </Button>
            <Button
              variant="outline"
              className="h-full min-h-[96px] justify-start whitespace-normal px-4 py-3 text-left"
              onClick={() => nav("/plugins/scheduler")}
            >
              <span>
                <span className="block font-medium">定时任务</span>
                <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                  按账号定时发消息、跑命令或调用 AI，适合固定周期的自动动作。
                </span>
              </span>
            </Button>
            <Button
              variant="outline"
              className="h-full min-h-[96px] justify-start whitespace-normal px-4 py-3 text-left"
              onClick={() => nav("/plugins/auto-command-whitelist")}
            >
              <span>
                <span className="block font-medium">自动命令白名单</span>
                <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                  控制定时任务和自动动作能触发哪些命令，避免误执行高风险操作。
                </span>
              </span>
            </Button>
            <Button
              variant="outline"
              className={`h-full min-h-[96px] justify-start whitespace-normal px-4 py-3 text-left ${
                guideActive ? "siri-glow" : ""
              }`}
              onClick={() => nav("/plugins/manage?tab=plugins")}
            >
              <span>
                <span className="block font-medium">安装模块</span>
                <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                  添加 Git 仓库安装远程模块；安装完成后回到本页按账号启用和配置。
                </span>
              </span>
            </Button>
          </div>
          <div className="rounded-lg border px-4 py-3">
            <div className="text-sm font-medium">AI 模块入口</div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              AI 能力属于模块配置：先在模型提供商里配置凭据，再按账号在模块里调用；调用记录与排障在 AI 用量查看。
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" onClick={() => nav("/ai")}>
                AI 总览
              </Button>
              <Button size="sm" variant="outline" onClick={() => nav("/ai/providers")}>
                模型提供商
              </Button>
              <Button size="sm" variant="outline" onClick={() => nav("/ai/usage")}>
                AI 用量
              </Button>
              <Button size="sm" variant="outline" onClick={() => nav("/ai/help")}>
                AI 帮助
              </Button>
            </div>
          </div>
          {guideActive ? (
          <GuideContextCard
            expanded={guideExpanded}
            onToggle={() => setGuideExpanded((v) => !v)}
            onInstall={() => nav("/plugins/manage?tab=plugins&guide=1")}
            onDone={() => {
              if (typeof window !== "undefined") {
                localStorage.setItem("telebot.accounts.new_account_guide_seen.v4", "1");
              }
              const next = new URLSearchParams(searchParams);
              next.delete("guide");
              nav(`/plugins${next.toString() ? `?${next.toString()}` : ""}`, { replace: true });
              setGuideExpanded(false);
            }}
          />
          ) : null}

        </CardContent>
      </Card>

      {codexImageFeature && codexImageState === "failed" ? (
        <Card className="border-amber-500/40 bg-amber-50/60">
          <CardHeader className="pb-2">
            <CardTitle className="text-base text-amber-900">codex_image 兼容提示</CardTitle>
            <CardDescription className="text-amber-800">
              当前账号历史上启用了 codex_image，但运行节点未检测到本地实现。系统已自动降级为失败态并保持 worker 持续运行。
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-0 text-sm text-amber-900">
            如需恢复，请确认目标节点是否已安装 codex_image 对应插件包，或先在该账号关闭此功能开关。
          </CardContent>
        </Card>
      ) : null}

      <Card
        className={`transition ${
          guideActive ? "siri-glow-soft" : ""
        }`}
      >
        <CardHeader>
          <CardTitle>账号模块启用详情与配置</CardTitle>
          <CardDescription>
            先选择要配置的账号，再查看每类模块在该账号上的启用状态与配置入口。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {accounts.length > 0 ? (
            <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
              <span className="text-sm text-muted-foreground">选择配置的账号：</span>
              <Select
                value={selectedAid?.toString() ?? ""}
                onChange={(e) => setSelectedAid(Number(e.target.value))}
                className="w-full sm:w-64"
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </Select>
            </div>
          ) : null}
          <div className="grid gap-4 lg:grid-cols-2">
            <FeatureZone
              title="平台能力"
              hint="系统级基础模块，入口集中在这里；需要时可进入配置或查看运行状态。"
              icon={<Settings2 className="h-4 w-4" />}
              features={grouped.platform}
              selectedAccountId={selectedAccount?.id}
              selectedFeatures={selectedAccount?.features ?? {}}
              onConfigure={openGenericConfig}
            />
            <FeatureZone
              title="内置模块"
              hint="常用自动化模块，按账号开启后再配置规则。"
              icon={<Package2 className="h-4 w-4" />}
              features={grouped.builtin}
              selectedAccountId={selectedAccount?.id}
              selectedFeatures={selectedAccount?.features ?? {}}
              onConfigure={openGenericConfig}
            />
            <FeatureZone
              title="远程模块"
              hint="从外部仓库安装的扩展模块能力。"
              icon={<SatelliteDish className="h-4 w-4" />}
              features={grouped.remote}
              selectedAccountId={selectedAccount?.id}
              selectedFeatures={selectedAccount?.features ?? {}}
              onConfigure={openGenericConfig}
            />
            <FeatureZone
              title="实验性"
              hint="还在试验中的能力，适合先小范围账号测试。"
              icon={<FlaskConical className="h-4 w-4" />}
              features={grouped.experimental}
              selectedAccountId={selectedAccount?.id}
              selectedFeatures={selectedAccount?.features ?? {}}
              onConfigure={openGenericConfig}
            />
          </div>
        </CardContent>
      </Card>
      <ConfigDialog
        open={!!configDialog}
        onOpenChange={(v) => !v && setConfigDialog(null)}
        pluginKey={configDialog?.key ?? ""}
        pluginName={configDialog?.name ?? ""}
        schema={(configDialog?.schema as unknown as ConfigSchema) ?? null}
        accountName={selectedAccount?.name}
        accountId={selectedAccount?.id}
        globalConfig={configDialog?.globalConfig ?? {}}
        accountConfig={configDialog?.accountConfig ?? {}}
        onSave={async (globalVals, accountVals) => {
          if (!configDialog || !selectedAccount) return;
          const schema = configDialog.schema as unknown as ConfigSchema | null;
          if (schema?.properties) {
            const globalFields = Object.entries(schema.properties)
              .filter(([, f]) => f.level === "global")
              .map(([k]) => k);
            const hasGlobalChanges = globalFields.some(
              (k) => globalVals[k] !== configDialog.globalConfig[k],
            );
            if (hasGlobalChanges) {
              const globalOnlyVals: Record<string, unknown> = {};
              for (const k of globalFields) {
                globalOnlyVals[k] = globalVals[k];
              }
              await setPluginGlobalConfig(configDialog.key, globalOnlyVals);
            }
          }
          if (Object.keys(accountVals).length > 0) {
            await updateAccountFeatureConfig(selectedAccount.id, configDialog.key, accountVals);
          }
          qc.invalidateQueries({ queryKey: ["account", selectedAccount.id, "features"] });
          qc.invalidateQueries({ queryKey: ["matrix"] });
          qc.invalidateQueries({ queryKey: ["plugin", "global", configDialog.key] });
        }}
      />
    </div>
  );
}

function GuideContextCard({
  expanded,
  onToggle,
  onInstall,
  onDone,
}: {
  expanded: boolean;
  onToggle: () => void;
  onInstall: () => void;
  onDone: () => void;
}) {
  const percent = 100;

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={onToggle}
        className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary shadow-sm shadow-primary/20 transition hover:bg-primary/15"
        aria-label="打开新手指引"
      >
        <Sparkles className="h-4 w-4" />
        新手指引：当前第 3 步，点击展开详情
      </button>
    );
  }

  return (
    <div className="max-w-lg rounded-2xl border bg-card/95 p-4 shadow-lg shadow-primary/10 backdrop-blur">
      <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>新手指引</span>
        <button type="button" onClick={onToggle} className="hover:text-foreground">
          收起
        </button>
      </div>
      <div className="mb-2 text-sm font-semibold">3. 启用命令模板或调用模块</div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        这一页主要看三处：先用“命令模板”复用命令；再看下方模块卡片，按账号启用和配置；需要外部能力时点“安装模块”添加远程模块。
      </p>
      <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
        <div className="rounded-lg border bg-muted/30 p-2">A. 命令模板</div>
        <div className="rounded-lg border bg-muted/30 p-2">B. 模块启用状态</div>
        <div className="rounded-lg border bg-muted/30 p-2">C. 安装模块</div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-all"
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" onClick={onInstall}>
          安装远程模块 <ArrowRight className="ml-1 h-4 w-4" />
        </Button>
        <Button size="sm" variant="outline" onClick={onDone}>
          我学会了！
        </Button>
      </div>
    </div>
  );
}

function FeatureZone({
  title,
  hint,
  icon,
  features,
  selectedAccountId,
  selectedFeatures,
  onConfigure,
}: {
  title: string;
  hint: string;
  icon: React.ReactNode;
  features: FeatureInfo[];
  selectedAccountId?: number;
  selectedFeatures: Record<string, string>;
  onConfigure: (feature: FeatureInfo) => void;
}) {
  const nav = useNavigate();

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          {icon}
          {title}
          <Badge variant="secondary">{features.length}</Badge>
        </CardTitle>
        <CardDescription>{hint}</CardDescription>
      </CardHeader>
      <CardContent>
        {features.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无内容</p>
        ) : (
          <div className="space-y-2">
            {features.map((f) => {
              const status = selectedFeatures[f.key] ?? "disabled";
              const path = featureConfigPath(selectedAccountId, f.key);
              const canConfigure = Boolean(path || f.config_schema);
              return (
                <div key={f.key} className="flex items-center justify-between rounded-md border p-2">
                  <div>
                    <div className="text-sm font-medium">{f.display_name}</div>
                    <div className="font-mono text-xs text-muted-foreground">{f.key}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={status === "active" ? "default" : "outline"}>
                      {status === "active" ? "已启用" : "未启用"}
                    </Badge>
                    {canConfigure ? (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          if (path) {
                            nav(path);
                            return;
                          }
                          onConfigure(f);
                        }}
                      >
                        配置
                      </Button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
