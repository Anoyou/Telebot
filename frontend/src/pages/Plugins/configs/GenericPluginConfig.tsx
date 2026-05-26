import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2, Save } from "lucide-react";
import { toast } from "sonner";

import { getAccount, listAccountFeatures, toggleAccountFeature } from "@/api/accounts";
import {
  getFeatureMatrix,
  getPluginGlobalConfig,
  setPluginGlobalConfig,
  updateAccountFeatureConfig,
} from "@/api/features";
import { getSystemSettings } from "@/api/system";
import {
  buildScopedConfigValues,
  ConfigScopeSection,
  type ConfigField,
  type ConfigSchema,
  withoutReadOnlyValues,
} from "@/components/plugin/ConfigDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import { Switch } from "@/components/ui/switch";
import { getErrMsg } from "@/lib/api";
import { featureConfigBackTarget } from "@/pages/Plugins/_shared/featureConfig";
import { featureRuntimeText, featureSwitchText } from "./_shared/featureStatus";

function isConfigSchema(schema: unknown): schema is ConfigSchema {
  const candidate = schema as Record<string, unknown> | null | undefined;
  return Boolean(
    candidate &&
      candidate.type === "object" &&
      candidate.properties &&
      typeof candidate.properties === "object" &&
      !Array.isArray(candidate.properties),
  );
}

function sameConfig(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function GenericPluginConfigPage() {
  const params = useParams();
  const aid = Number(params.aid);
  const featureKey = params.featureKey ?? "";
  const nav = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();

  const accountQ = useQuery({
    queryKey: ["account", aid],
    queryFn: () => getAccount(aid),
    enabled: !!aid,
  });
  const matrixQ = useQuery({
    queryKey: ["matrix"],
    queryFn: getFeatureMatrix,
  });
  const featuresQ = useQuery({
    queryKey: ["account", aid, "features"],
    queryFn: () => listAccountFeatures(aid),
    enabled: !!aid,
  });
  const globalConfigQ = useQuery({
    queryKey: ["plugin", "global", featureKey],
    queryFn: () => getPluginGlobalConfig(featureKey),
    enabled: !!featureKey,
  });
  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });

  const feature = matrixQ.data?.features.find((item) => item.key === featureKey);
  const accountFeature = featuresQ.data?.find((item) => item.feature_key === featureKey);
  const schema = isConfigSchema(feature?.config_schema) ? feature.config_schema : null;
  const globalConfig = globalConfigQ.data ?? {};
  const accountConfig = accountFeature?.config ?? {};
  const commandPrefix = settingsQ.data?.command_prefix || ",";

  const [globalVals, setGlobalVals] = useState<Record<string, unknown>>({});
  const [accountVals, setAccountVals] = useState<Record<string, unknown>>({});
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!schema) return;
    const next = buildScopedConfigValues(schema, globalConfig, accountConfig);
    setGlobalVals(next.globalVals);
    setAccountVals(next.accountVals);
    setDirty(false);
  }, [schema, globalConfig, accountConfig]);

  const { globalFields, accountFields } = useMemo(() => {
    const properties = schema?.properties ?? {};
    return {
      globalFields: Object.entries(properties).filter(([, field]) => field.level === "global") as Array<[string, ConfigField]>,
      accountFields: Object.entries(properties).filter(([, field]) => field.level !== "global") as Array<[string, ConfigField]>,
    };
  }, [schema]);
  const usageGuide = useMemo(
    () => buildUsageGuide({
      schema,
      values: { ...globalVals, ...accountVals },
      commandPrefix,
      interactionEntries: feature?.interaction_entries,
    }),
    [schema, globalVals, accountVals, commandPrefix, feature?.interaction_entries],
  );

  const saveMut = useMutation({
    mutationFn: async () => {
      if (!schema) return;
      const properties = schema.properties;
      const editableGlobalVals = withoutReadOnlyValues(globalVals, properties, globalConfig);
      const editableAccountVals = withoutReadOnlyValues(accountVals, properties, accountConfig);

      if (globalFields.length > 0) {
        const globalOnlyVals: Record<string, unknown> = {};
        for (const [key] of globalFields) {
          if (key in editableGlobalVals) globalOnlyVals[key] = editableGlobalVals[key];
        }
        if (!sameConfig(globalOnlyVals, globalConfig)) {
          await setPluginGlobalConfig(featureKey, globalOnlyVals);
        }
      }

      if (accountFields.length > 0) {
        const accountOnlyVals: Record<string, unknown> = {};
        for (const [key] of accountFields) {
          if (key in editableAccountVals) accountOnlyVals[key] = editableAccountVals[key];
        }
        if (!sameConfig(accountOnlyVals, accountConfig)) {
          await updateAccountFeatureConfig(aid, featureKey, accountOnlyVals);
        }
      }
    },
    onSuccess: () => {
      toast.success("配置已保存（worker 热加载）");
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["account", aid, "features"] });
      qc.invalidateQueries({ queryKey: ["plugin", "global", featureKey] });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const toggleMut = useMutation({
    mutationFn: (enabled: boolean) => toggleAccountFeature(aid, featureKey, enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", aid, "features"] });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (!aid) return <p>账号 ID 不合法</p>;
  if (!featureKey) return <p>功能 key 不合法</p>;
  if (matrixQ.isLoading || featuresQ.isLoading || accountQ.isLoading || globalConfigQ.isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  const accountLabel =
    accountQ.data?.display_name ||
    (accountQ.data?.tg_username ? `@${accountQ.data.tg_username}` : `#${aid}`);
  const hasSchemaFields = Boolean(schema && Object.keys(schema.properties).length > 0);
  const backTarget = featureConfigBackTarget(aid, location.search);

  function resetForm() {
    if (!schema) return;
    const next = buildScopedConfigValues(schema, globalConfig, accountConfig);
    setGlobalVals(next.globalVals);
    setAccountVals(next.accountVals);
    setDirty(false);
  }

  return (
    <div className="space-y-6 pb-24">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => nav(backTarget.backHref)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> {backTarget.backLabel}
        </Button>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {feature?.display_name ?? featureKey}
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <code>{featureKey}</code>
            <span>账号：{accountLabel}</span>
            {feature ? (
              <Badge variant={feature.is_builtin ? "secondary" : "outline"}>
                {feature.is_builtin ? "内置" : "第三方"}
              </Badge>
            ) : null}
          </div>
        </div>
      </div>

      {hasSchemaFields ? (
        <div className="sticky top-0 z-30 -mx-2 rounded-b-lg border bg-background/95 px-2 py-3 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-background/80">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm">
              <div className="font-medium">配置操作</div>
              <div className="text-xs text-muted-foreground">
                {dirty ? "有未保存修改，保存后 worker 会热加载。" : "当前配置已同步。"}
              </div>
            </div>
            <div className="flex items-center gap-4">
              <Button onClick={() => saveMut.mutate()} disabled={saveMut.isPending || !dirty}>
                {saveMut.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Save className="mr-2 h-4 w-4" />
                )}
                保存配置
              </Button>
              <Button type="button" variant="ghost" disabled={!dirty || saveMut.isPending} onClick={resetForm} className="px-0">
                撤销
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">使用说明</CardTitle>
          <CardDescription>{usageGuide.description}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3 rounded-md border bg-muted/20 p-3 text-xs text-muted-foreground">
            {usageGuide.customText ? (
              <div className="whitespace-pre-wrap leading-relaxed text-foreground">
                {usageGuide.customText}
              </div>
            ) : null}
            {usageGuide.commandExamples.length > 0 ? (
              <div>
                <div className="mb-1 font-medium text-foreground">常用命令</div>
                <div className="space-y-1">
                  {usageGuide.commandExamples.map((item) => (
                    <div key={item} className="rounded border bg-background px-2 py-1 font-mono text-[11px] text-foreground">
                      {item}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            <ul className="list-inside list-disc space-y-1">
              {usageGuide.notes.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="text-base">功能总开关</CardTitle>
              <CardDescription>关闭后模块不会在当前账号运行；配置保存后会由 worker 热加载。</CardDescription>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant={accountFeature?.enabled ? "default" : "outline"}>
                  {featureSwitchText(accountFeature)}
                </Badge>
                <span>运行状态：{featureRuntimeText(accountFeature)}</span>
                {accountFeature?.last_error ? (
                  <span className="text-destructive">最近错误：{accountFeature.last_error}</span>
                ) : null}
              </div>
            </div>
            <Switch
              checked={Boolean(accountFeature?.enabled)}
              disabled={toggleMut.isPending || !accountFeature}
              onCheckedChange={(enabled) => toggleMut.mutate(enabled)}
            />
          </div>
        </CardHeader>
      </Card>

      {!hasSchemaFields ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">配置</CardTitle>
            <CardDescription>该功能没有可配置的 Schema 字段。</CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">配置</CardTitle>
            <CardDescription>全局配置对所有账号共享，账号配置仅影响当前账号。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {globalFields.length > 0 ? (
              <ConfigScopeSection
                title="全局配置"
                description="所有账号共享"
                fields={globalFields}
                values={globalVals}
                commandPrefix={commandPrefix}
                onChange={(key, value) => {
                  setGlobalVals((prev) => ({ ...prev, [key]: value }));
                  setDirty(true);
                }}
              />
            ) : null}
            {accountFields.length > 0 ? (
              <ConfigScopeSection
                title="账号配置"
                description={`${accountLabel} 专属`}
                fields={accountFields}
                values={accountVals}
                commandPrefix={commandPrefix}
                onChange={(key, value) => {
                  setAccountVals((prev) => ({ ...prev, [key]: value }));
                  setDirty(true);
                }}
              />
            ) : null}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

interface UsageGuide {
  description: string;
  customText: string;
  commandExamples: string[];
  notes: string[];
}

function buildUsageGuide({
  schema,
  values,
  commandPrefix,
  interactionEntries,
}: {
  schema: ConfigSchema | null;
  values: Record<string, unknown>;
  commandPrefix: string;
  interactionEntries?: Array<{ title?: string | null; description?: string | null; key?: string | null }>;
}): UsageGuide {
  const properties = schema?.properties ?? {};
  const command = configString(values.command ?? properties.command?.default) || "command";
  const sample = { prefix: commandPrefix || ",", command };
  const customText = renderUsageText(configString(values.usage_preview ?? properties.usage_preview?.default), sample);
  const aliasExamples = buildCommandExamples(properties, values, sample.prefix, command);
  const interactionNotes = (interactionEntries ?? [])
    .map((entry) => {
      const title = entry.title || entry.key;
      const description = entry.description;
      if (!title && !description) return "";
      return `可交互：${[title, description].filter(Boolean).join("，")}`;
    })
    .filter(Boolean);

  const notes = [
    ...interactionNotes,
    "全局配置对所有账号共享，账号配置仅影响当前账号。",
    "命令类字段只填写命令名，不需要包含系统命令前缀。",
    "消息模板字段可使用页面中的占位符和预览检查最终发送效果。",
  ];

  return {
    description: customText ? "来自模块 schema 的使用说明；保存后由 worker 热加载。" : "按字段填写当前账号的模块配置，保存后由 worker 热加载。",
    customText,
    commandExamples: aliasExamples.length > 0 ? aliasExamples : [`${sample.prefix}${command}`],
    notes,
  };
}

function buildCommandExamples(
  properties: Record<string, ConfigField>,
  values: Record<string, unknown>,
  prefix: string,
  command: string,
): string[] {
  const knownArgs: Record<string, string> = {
    buy_aliases: "3 5",
    history_aliases: "5",
    sponsor_aliases: "10000",
    unsponsor_aliases: "10000",
    refund_aliases: "1",
  };
  const priority = [
    "help_aliases",
    "buy_aliases",
    "my_aliases",
    "pool_aliases",
    "hot_aliases",
    "stats_aliases",
    "history_aliases",
    "draw_aliases",
    "reset_aliases",
    "sponsor_aliases",
    "unsponsor_aliases",
    "refund_aliases",
  ];
  const aliasKeys = Object.keys(properties).filter((key) => /(^|_)aliases$/i.test(key));
  const orderedKeys = [
    ...priority.filter((key) => aliasKeys.includes(key)),
    ...aliasKeys.filter((key) => !priority.includes(key)).sort(),
  ];
  return orderedKeys
    .map((key) => {
      const alias = firstAlias(values[key] ?? properties[key]?.default);
      if (!alias) return "";
      const suffix = knownArgs[key] ? ` ${knownArgs[key]}` : "";
      return `${prefix}${command} ${alias}${suffix}`;
    })
    .filter(Boolean)
    .slice(0, 8);
}

function renderUsageText(value: string, sample: { prefix: string; command: string }): string {
  return normalizeUsageEscapes(value)
    .replace(/\{prefix\}/g, sample.prefix)
    .replace(/\{command\}/g, sample.command)
    .trim();
}

function normalizeUsageEscapes(value: string): string {
  return value
    .replace(/\\r\\n/g, "\n")
    .replace(/\\n/g, "\n")
    .replace(/\\r/g, "\n");
}

function firstAlias(value: unknown): string {
  return configString(value).split(/\s+/).map((item) => item.trim()).find(Boolean) || "";
}

function configString(value: unknown): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map((item) => String(item)).join(" ");
  return String(value);
}
