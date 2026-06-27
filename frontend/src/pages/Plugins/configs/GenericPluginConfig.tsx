import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Loader2, Save } from "lucide-react";
import { toast } from "sonner";

import { getAccount, listAccountFeatures, toggleAccountFeature } from "@/api/accounts";
import { listLLMProviders } from "@/api/commands";
import {
  getFeatureMatrix,
  getPluginGlobalConfig,
  setPluginGlobalConfig,
  updateAccountFeatureConfig,
} from "@/api/features";
import { getSystemSettings } from "@/api/system";
import {
  buildScopedConfigValues,
  ConfigPreviewSection,
  ConfigScopeSection,
  schemaHasLLMSelect,
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
import { pluginUsageGuideWarning } from "@/lib/plugin-config-contract";
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
  const llmProvidersQ = useQuery({
    queryKey: ["llm-providers"],
    queryFn: listLLMProviders,
    enabled: Boolean(schema && schemaHasLLMSelect(schema)),
  });

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

  const { globalFields, accountFields, previewFields } = useMemo(() => {
    const properties = schema?.properties ?? {};
    const isGuideField = (key: string) =>
      key === "usage_preview" ||
      key === "usage_guide" ||
      key === "usage_instructions" ||
      key === "ai_usage_guide" ||
      key === "template_placeholders" ||
      key === "template_preview" ||
      /_preview$/i.test(key);
    const isUsageOnlyField = (key: string) =>
      key === "usage_preview" ||
      key === "usage_guide" ||
      key === "usage_instructions" ||
      key === "ai_usage_guide" ||
      key === "template_placeholders";
    const entries = Object.entries(properties) as Array<[string, ConfigField]>;
    return {
      globalFields: entries.filter(
        ([key, field]) => !isGuideField(key) && field.level === "global",
      ),
      accountFields: entries.filter(
        ([key, field]) => !isGuideField(key) && field.level !== "global",
      ),
      previewFields: entries.filter(([key]) => !isUsageOnlyField(key)),
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
      qc.invalidateQueries({ queryKey: ["message-templates", "catalog"] });
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

      <Card>
        <CardHeader>
          <CardTitle className="text-base">使用说明</CardTitle>
          <CardDescription>{usageGuide.description}</CardDescription>
        </CardHeader>
        <CardContent>
          {usageGuide.missing ? (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <div className="font-medium">高级规范警告</div>
                <div className="mt-1 text-xs leading-5">
                  {usageGuide.warning}
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-3 rounded-md border bg-muted/20 p-3 text-xs text-muted-foreground">
              <div className="whitespace-pre-wrap leading-relaxed text-foreground">
                {usageGuide.customText}
              </div>
              {usageGuide.commandExamples.length > 0 ? (
                <div>
                  <div className="mb-1 font-medium text-foreground">插件声明的指令参考</div>
                  <div className="space-y-1">
                    {usageGuide.commandExamples.map((item) => (
                      <div key={item} className="rounded border bg-background px-2 py-1 font-mono text-[11px] text-foreground">
                        {item}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              {usageGuide.notes.length > 0 ? (
                <ul className="list-inside list-disc space-y-1">
                  {usageGuide.notes.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="text-base">功能总开关</CardTitle>
              <CardDescription>关闭后插件不会在当前账号运行；配置保存后会由 worker 热加载。</CardDescription>
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
            <CardTitle className="text-base">插件配置</CardTitle>
            <CardDescription>该功能没有可配置的 Schema 字段。</CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">插件配置</CardTitle>
            <CardDescription>字段由插件声明的 config_schema 渲染；保存后由 worker 热加载。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 pb-0">
            {globalFields.length > 0 ? (
              <ConfigScopeSection
                title="全局配置"
                description="所有账号共享，适合 Token、Provider、公共模板等跨账号配置。"
                fields={globalFields}
                values={globalVals}
                commandPrefix={commandPrefix}
                llmProviders={llmProvidersQ.data}
                llmProvidersLoading={llmProvidersQ.isLoading || llmProvidersQ.isFetching}
                showPreviews={false}
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
                llmProviders={llmProvidersQ.data}
                llmProvidersLoading={llmProvidersQ.isLoading || llmProvidersQ.isFetching}
                showPreviews={false}
                onChange={(key, value) => {
                  setAccountVals((prev) => ({ ...prev, [key]: value }));
                  setDirty(true);
                }}
              />
            ) : null}
          </CardContent>
          <div className="static z-20 mt-4 rounded-b-lg border-t bg-background/95 px-4 py-3 shadow-[0_-8px_20px_rgba(15,23,42,0.06)] backdrop-blur supports-[backdrop-filter]:bg-background/85 sm:sticky sm:bottom-0 sm:px-6">
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
        </Card>
      )}

      {hasSchemaFields ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">插件预览</CardTitle>
            <CardDescription>
              插件可选声明的模板预览，使用模拟上下文渲染，不触发真实发送。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ConfigPreviewSection
              fields={previewFields}
              values={{ ...globalVals, ...accountVals }}
              commandPrefix={commandPrefix}
            />
            {!hasPreviewFields(previewFields) ? (
              <div className="rounded-md border border-dashed bg-muted/20 px-3 py-4 text-sm text-muted-foreground">
                当前插件没有声明预览字段。建议在 schema 中提供 <code>template_preview</code> 或 <code>*_preview</code>，便于用户确认最终消息效果。
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

interface UsageGuide {
  description: string;
  customText: string;
  commandExamples: string[];
  notes: string[];
  missing: boolean;
  warning: string;
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
  const usageVariables = buildUsageVariables(properties, values, commandPrefix || ",", command);
  const customText = renderUsageText(
    firstUsageGuideText([
      schema?.["x-usage-guide"],
      schema?.["x-usage-instructions"],
      schema?.["x-usage-steps"],
      schema?.["x-help"],
      values.usage_preview ?? properties.usage_preview?.default,
      values.usage_guide ?? properties.usage_guide?.default,
      values.usage_instructions ?? properties.usage_instructions?.default,
      values.ai_usage_guide ?? properties.ai_usage_guide?.default,
      values.template_placeholders ?? properties.template_placeholders?.default,
    ]),
    usageVariables,
  );
  const aliasExamples = buildCommandExamples(properties, values, usageVariables.prefix, command);
  const missingWarning = pluginUsageGuideWarning({ config_schema: schema });
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
  ];

  return {
    description: missingWarning ? "该插件缺少自声明使用说明，需要插件开发者补齐。" : "来自插件 schema 的自声明使用说明。",
    customText,
    commandExamples: customText ? aliasExamples : [],
    notes,
    missing: Boolean(missingWarning),
    warning: missingWarning ?? "",
  };
}

function hasPreviewFields(fields: Array<[string, ConfigField]>): boolean {
  return fields.some(([key]) => key === "template_preview" || /_preview$/i.test(key));
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

function renderUsageText(value: string, variables: Record<string, string>): string {
  let text = normalizeUsageEscapes(value);
  for (const [key, replacement] of Object.entries(variables)) {
    text = text.replace(new RegExp(`\\{${escapeRegExp(key)}\\}`, "g"), replacement);
  }
  return text.trim();
}

function buildUsageVariables(
  properties: Record<string, ConfigField>,
  values: Record<string, unknown>,
  prefix: string,
  command: string,
): Record<string, string> {
  const variables: Record<string, string> = { prefix, command };
  for (const [key, field] of Object.entries(properties)) {
    if (isSensitiveUsageVariableKey(key)) continue;
    const text = usageVariableText(values[key] ?? field.default);
    if (text) variables[key] = text;
  }
  return variables;
}

function firstUsageGuideText(values: unknown[]): string {
  for (const value of values) {
    const text = formatUsageGuideValue(value).trim();
    if (text) return text;
  }
  return "";
}

function normalizeUsageEscapes(value: string): string {
  return value
    .replace(/\\r\\n/g, "\n")
    .replace(/\\n/g, "\n")
    .replace(/\\r/g, "\n");
}

function formatUsageGuideValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map((item) => formatUsageGuideValue(item)).filter(Boolean).join("\n");
  }
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function usageVariableText(value: unknown): string {
  if (value == null || Array.isArray(value) || typeof value === "object") return "";
  return String(value);
}

function isSensitiveUsageVariableKey(key: string): boolean {
  return /(^|_)(api_key|access_token|auth_token|bot_token|token|tokens|secret|password|passwd|pwd)$/i.test(key);
}

function firstAlias(value: unknown): string {
  return configString(value).split(/\s+/).map((item) => item.trim()).find(Boolean) || "";
}

function configString(value: unknown): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map((item) => String(item)).join(" ");
  return String(value);
}
