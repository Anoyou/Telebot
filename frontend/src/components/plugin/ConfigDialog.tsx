/**
 * 插件配置弹窗组件。
 *
 * 根据插件的 config_schema 自动渲染表单：
 * - level: "global" 的字段 → 全局配置区
 * - level: "account" 的字段 → 账号配置区
 * - 无 level → 默认 account
 *
 * 配置合并顺序（前端用于展示）：
 * schema defaults < globalConfig < accountConfig
 */
import { useState, useEffect, useCallback, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Save } from "lucide-react";
import { toast } from "sonner";
import { TelegramHtmlPreview, TelegramHtmlPreviewThread } from "@/components/TelegramHtmlPreview";
import { listLLMProviders } from "@/api/commands";
import { getSystemSettings } from "@/api/system";
import type { LLMProviderOut } from "@/api/types";
import { getErrMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

export const MASKED_SECRET_PLACEHOLDER = "••••••••••••••••";

export interface ConfigField {
  key: string;
  title?: string;
  type: string;
  format?: string;
  "x-ui-widget"?: string;
  "x-ui-section"?: string;
  "x-ui-order"?: number;
  "x-ui-columns"?: 1 | 2 | 3 | number;
  "x-ui-provider-field"?: string;
  "x-ui-model-modality"?: string;
  enum?: Array<string | number | boolean>;
  enumNames?: string[];
  enumDescriptions?: string[];
  items?: { type?: string };
  default?: unknown;
  description?: string;
  minimum?: number;
  maximum?: number;
  level?: "global" | "account";
  readOnly?: boolean;
}

export interface ConfigSchema {
  type: string;
  properties: Record<string, ConfigField>;
  required?: string[];
  "x-usage-guide"?: unknown;
  "x-usage-instructions"?: unknown;
  "x-usage-steps"?: unknown;
  "x-help"?: unknown;
}

interface ConfigDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pluginKey: string;
  pluginName: string;
  schema: ConfigSchema | Record<string, unknown> | null;
  accountName?: string;
  accountId?: number;
  globalConfig?: Record<string, unknown>;
  accountConfig?: Record<string, unknown>;
  /** 保存回调，返回更新后的 effective config */
  onSave?: (globalVals: Record<string, unknown>, accountVals: Record<string, unknown>) => Promise<void>;
}

export function ConfigDialog({
  open, onOpenChange, pluginKey, pluginName, schema, accountName,
  accountId, globalConfig = {}, accountConfig = {}, onSave,
}: ConfigDialogProps) {
  const [globalVals, setGlobalVals] = useState<Record<string, unknown>>({});
  const [accountVals, setAccountVals] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
    enabled: open,
  });
  const hasLLMSelect = schemaHasLLMSelect(schema);
  const llmProvidersQ = useQuery({
    queryKey: ["llm-providers"],
    queryFn: listLLMProviders,
    enabled: open && hasLLMSelect,
  });
  const commandPrefix = settingsQ.data?.command_prefix || ",";

  const handleSave = useCallback(async () => {
    if (!onSave) return;
    const properties = ((schema as ConfigSchema | null)?.properties ?? {}) as Record<string, ConfigField>;
    const editableGlobalVals = withoutReadOnlyValues(globalVals, properties, globalConfig);
    const editableAccountVals = withoutReadOnlyValues(accountVals, properties, accountConfig);
    setSaving(true);
    try {
      await onSave(editableGlobalVals, editableAccountVals);
      toast.success("配置已保存");
      onOpenChange(false);
    } catch (err) {
      toast.error(getErrMsg(err));
    } finally {
      setSaving(false);
    }
  }, [onSave, schema, globalVals, accountVals, onOpenChange]);

  // 初始化配置值
  useEffect(() => {
    if (open && schema && typeof schema === "object" && "properties" in schema) {
      const s = schema as ConfigSchema;
      const { globalVals: gv, accountVals: av } = buildScopedConfigValues(s, globalConfig, accountConfig);
      setGlobalVals(gv);
      setAccountVals(av);
    }
  }, [open, schema, globalConfig, accountConfig]);

  const s = schema as ConfigSchema | null;
  if (!s?.properties || Object.keys(s.properties).length === 0) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{pluginName} — 配置</DialogTitle>
            <DialogDescription>该插件没有可配置的选项。</DialogDescription>
          </DialogHeader>
          <DialogFooter><Button onClick={() => onOpenChange(false)}>关闭</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  const globalFields = Object.entries(s.properties).filter(([, f]) => f.level === "global");
  const accountFields = Object.entries(s.properties).filter(([, f]) => f.level !== "global");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{pluginName} — 配置</DialogTitle>
          <DialogDescription>
            插件: <code className="text-xs">{pluginKey}</code>
            {accountName && <> · 账号: {accountName}</>}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {globalFields.length > 0 && (
            <ConfigScopeSection
              title="全局配置"
              description="所有账号共享"
              fields={globalFields}
              values={globalVals}
              commandPrefix={commandPrefix}
              llmProviders={llmProvidersQ.data}
              llmProvidersLoading={llmProvidersQ.isLoading}
              onChange={(key, value) => setGlobalVals((p) => ({ ...p, [key]: value }))}
            />
          )}
          {accountFields.length > 0 && (
            <ConfigScopeSection
              title="账号配置"
              description={accountName ? accountName + " 专属" : "按账号隔离"}
              fields={accountFields}
              values={accountVals}
              commandPrefix={commandPrefix}
              llmProviders={llmProvidersQ.data}
              llmProvidersLoading={llmProvidersQ.isLoading}
              onChange={(key, value) => setAccountVals((p) => ({ ...p, [key]: value }))}
            />
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? <Spinner className="mr-2 h-4 w-4" /> : <Save className="mr-2 h-4 w-4" />}
            {saving ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export type FieldEntry = [string, ConfigField];

interface ConfigScopeSectionProps {
  title: string;
  description: string;
  fields: FieldEntry[];
  values: Record<string, unknown>;
  commandPrefix: string;
  llmProviders?: LLMProviderOut[];
  llmProvidersLoading?: boolean;
  showPreviews?: boolean;
  onChange: (key: string, value: unknown) => void;
}

export function ConfigScopeSection({
  title,
  description,
  fields,
  values,
  commandPrefix,
  llmProviders,
  llmProvidersLoading = false,
  showPreviews = true,
  onChange,
}: ConfigScopeSectionProps) {
  const [openTemplates, setOpenTemplates] = useState<Record<string, boolean>>({});
  const groups = groupConfigFields(fields);

  return (
    <section className="space-y-4 rounded-md border bg-muted/30 p-3">
      <div>
        <div className="text-sm font-semibold">{title}</div>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>

      {groups.sections.length > 0 && (
        <div className="space-y-3">
          {groups.sections.map((section) => (
            <div key={section.key} className="space-y-3 rounded-md border bg-background p-3">
              <div className="text-sm font-semibold">{section.title}</div>
              <div className={configGridClass(section.columns)}>
                {section.fields.map(([key, field]) => (
                  <FieldInput
                    key={key}
                    fk={key}
                    field={field}
                    value={values[key]}
                    values={values}
                    llmProviders={llmProviders}
                    llmProvidersLoading={llmProvidersLoading}
                    onChange={(value) => onChange(key, value)}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {groups.basic.length > 0 && (
        <div className="space-y-4">
          {groups.basic.map(([key, field]) => (
            <FieldInput
              key={key}
              fk={key}
              field={field}
              value={values[key]}
              values={values}
              llmProviders={llmProviders}
              llmProvidersLoading={llmProvidersLoading}
              onChange={(value) => onChange(key, value)}
            />
          ))}
        </div>
      )}

      {(groups.templates.length > 0 || groups.placeholders.length > 0) && (
        <div className="space-y-3 rounded-md border bg-background p-3">
          <div>
            <div className="text-sm font-semibold">消息模板</div>
            <p className="text-xs text-muted-foreground">点击展开后编辑对应消息。</p>
          </div>
          {groups.placeholders.map(([key, field]) => (
            <FieldInput
              key={key}
              fk={key}
              field={field}
              value={values[key]}
              values={values}
              llmProviders={llmProviders}
              llmProvidersLoading={llmProvidersLoading}
              onChange={(value) => onChange(key, value)}
            />
          ))}
          <div className="space-y-2">
            {groups.templates.map(([key, field]) => {
              const label = field.title || key;
              const open = Boolean(openTemplates[key]);
              return (
                <div key={key} className="rounded-md border bg-muted/20">
                  <button
                    type="button"
                    className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm font-medium"
                    onClick={() => setOpenTemplates((p) => ({ ...p, [key]: !p[key] }))}
                  >
                    <span>{label}</span>
                    {open ? (
                      <ChevronDown className="h-4 w-4 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-muted-foreground" />
                    )}
                  </button>
                  {open && (
                    <div className="border-t px-3 py-3">
                      <FieldInput
                        fk={key}
                        field={field}
                        value={values[key]}
                        values={values}
                        llmProviders={llmProviders}
                        llmProvidersLoading={llmProvidersLoading}
                        onChange={(value) => onChange(key, value)}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {showPreviews && groups.previews.length > 0 && (
        <div className="space-y-3 rounded-md border bg-background p-3">
          <div>
            <div className="text-sm font-semibold">预览结果</div>
            <p className="text-xs text-muted-foreground">按模板顺序展示一组 Telegram 消息气泡。</p>
          </div>
          <TelegramHtmlPreviewThread
            messages={groups.previews.map(([key, field]) => ({
              title: field.title || key,
              value: renderPreviewValue(key, field, fields, values, commandPrefix),
              mode: "html",
            }))}
          />
        </div>
      )}
    </section>
  );
}

export function ConfigPreviewSection({
  fields,
  values,
  commandPrefix,
}: {
  fields: FieldEntry[];
  values: Record<string, unknown>;
  commandPrefix: string;
}) {
  const groups = groupConfigFields(fields);
  if (groups.previews.length === 0) return null;
  return (
    <div className="space-y-3">
      <TelegramHtmlPreviewThread
        messages={groups.previews.map(([key, field]) => ({
          title: field.title || key,
          value: renderPreviewValue(key, field, fields, values, commandPrefix),
          mode: "html",
        }))}
      />
    </div>
  );
}

export function groupConfigFields(fields: FieldEntry[]): {
  basic: FieldEntry[];
  placeholders: FieldEntry[];
  templates: FieldEntry[];
  previews: FieldEntry[];
  sections: Array<{ key: string; title: string; fields: FieldEntry[]; columns: number }>;
} {
  const basic: FieldEntry[] = [];
  const placeholders: FieldEntry[] = [];
  const templates: FieldEntry[] = [];
  const previews: FieldEntry[] = [];
  const sectionsByKey = new Map<string, { key: string; title: string; fields: FieldEntry[]; columns: number }>();

  const sortedFields = [...fields].sort((a, b) => fieldOrder(a[1]) - fieldOrder(b[1]));

  for (const entry of sortedFields) {
    const [key, field] = entry;
    if (isPreviewField(key)) {
      previews.push(entry);
    } else if (isPlaceholderField(key)) {
      placeholders.push(entry);
    } else if (isTemplateField(key)) {
      templates.push(entry);
    } else {
      basic.push(entry);
      const sectionName = typeof field["x-ui-section"] === "string" ? field["x-ui-section"].trim() : "";
      if (sectionName) {
        const sectionKey = sectionName.toLowerCase();
        const current = sectionsByKey.get(sectionKey) ?? {
          key: sectionKey,
          title: sectionName,
          fields: [],
          columns: clampColumns(field["x-ui-columns"]),
        };
        current.fields.push(entry);
        current.columns = Math.max(current.columns, clampColumns(field["x-ui-columns"]));
        sectionsByKey.set(sectionKey, current);
      }
    }
  }

  const sectionFieldKeys = new Set(
    Array.from(sectionsByKey.values()).flatMap((section) => section.fields.map(([key]) => key)),
  );
  const unsectionedBasic = basic.filter(([key]) => !sectionFieldKeys.has(key));

  return {
    basic: unsectionedBasic,
    placeholders,
    templates,
    previews,
    sections: Array.from(sectionsByKey.values()),
  };
}

function fieldOrder(field: ConfigField): number {
  return Number.isFinite(field["x-ui-order"]) ? Number(field["x-ui-order"]) : 0;
}

function clampColumns(value: unknown): number {
  const raw = Number(value);
  if (!Number.isFinite(raw)) return 2;
  return Math.min(3, Math.max(1, Math.floor(raw)));
}

function configGridClass(columns: number): string {
  if (columns >= 3) return "grid gap-4 md:grid-cols-2 xl:grid-cols-3";
  if (columns === 2) return "grid gap-4 md:grid-cols-2";
  return "space-y-4";
}

interface FieldInputProps {
  fk: string;
  field: ConfigField;
  value: unknown;
  values?: Record<string, unknown>;
  llmProviders?: LLMProviderOut[];
  llmProvidersLoading?: boolean;
  previewValue?: string;
  onChange: (v: unknown) => void;
}

function FieldInput({
  fk,
  field,
  value,
  values = {},
  llmProviders,
  llmProvidersLoading = false,
  previewValue,
  onChange,
}: FieldInputProps) {
  const label = field.title || fk;
  const description = field.description;
  const inputId = `plugin-config-${fk}`;
  const textValue = formatConfigValue(value);
  const defaultValue = field.default != null ? formatConfigValue(field.default) : "";
  const isPreview = isPreviewField(fk);
  const isPlaceholders = isPlaceholderField(fk);
  const isTemplate = isTemplateField(fk);
  const isReadOnly = isReadOnlyField(fk, field);
  const isSensitive = isSensitiveConfigKey(fk);

  if (isPreview) {
    return (
      <ReadOnlyField label={label} description={description}>
        <TelegramHtmlPreview value={previewValue ?? (textValue || defaultValue)} mode="html" title={label} />
      </ReadOnlyField>
    );
  }

  if (isPlaceholders) {
    return (
      <ReadOnlyField label={label} description={description}>
        <pre className="whitespace-pre-wrap break-words font-sans text-sm text-muted-foreground">
          {textValue || defaultValue || "暂无占位符说明"}
        </pre>
      </ReadOnlyField>
    );
  }

  if (isReadOnly) {
    return (
      <ReadOnlyField label={label} description={description}>
        <pre className="whitespace-pre-wrap break-words font-sans text-sm text-muted-foreground">
          {textValue || defaultValue || "未设置"}
        </pre>
      </ReadOnlyField>
    );
  }

  if (field["x-ui-widget"] === "llm-provider-select") {
    return (
      <LLMProviderSelectField
        inputId={inputId}
        label={label}
        description={description}
        value={value}
        providers={llmProviders}
        loading={llmProvidersLoading}
        onChange={onChange}
      />
    );
  }

  if (field["x-ui-widget"] === "llm-model-select") {
    const providerField = field["x-ui-provider-field"];
    const providerValue = providerField ? values[providerField] : undefined;
    return (
      <LLMModelSelectField
        inputId={inputId}
        label={label}
        description={description}
        value={value}
        providerValue={providerValue}
        providers={llmProviders}
        loading={llmProvidersLoading}
        modelModality={field["x-ui-model-modality"]}
        onChange={onChange}
      />
    );
  }

  if (field.enum && field.enum.length > 0) {
    const enumLabels = field.enumNames ?? [];
    return (
      <div className="space-y-1.5">
        <Label htmlFor={inputId}>{label}</Label>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
        <Select
          id={inputId}
          value={value != null ? String(value) : ""}
          onChange={(e) => onChange(e.target.value)}
        >
          {!field.enum.some((v) => String(v) === "") && <option value="">未设置</option>}
          {field.enum.map((opt, index) => (
            <option key={String(opt)} value={String(opt)}>
              {enumLabels[index] || String(opt)}
            </option>
          ))}
        </Select>
        {field.enumDescriptions && field.enumDescriptions.length > 0 && (
          <ul className="space-y-1 text-xs text-muted-foreground">
            {field.enum.map((opt, index) => (
              field.enumDescriptions?.[index] ? (
                <li key={String(opt)}>
                  <span className="font-medium">{enumLabels[index] || String(opt)}</span>
                  ：{field.enumDescriptions[index]}
                </li>
              ) : null
            ))}
          </ul>
        )}
      </div>
    );
  }

  if (field.type === "boolean") {
    return (
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1.5">
          <Label htmlFor={inputId}>{label}</Label>
          {description && <p className="text-xs text-muted-foreground">{description}</p>}
        </div>
        <Switch
          id={inputId}
          checked={Boolean(value)}
          onCheckedChange={onChange}
        />
      </div>
    );
  }

  if (field.type === "integer" || field.type === "number") {
    return (
      <div className="space-y-1.5">
        <Label htmlFor={inputId}>{label}</Label>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
        <Input
          id={inputId}
          type="number"
          value={value != null ? String(value) : ""}
          min={field.minimum as number}
          max={field.maximum as number}
          onChange={(e) => {
            const v = e.target.value;
            onChange(v === "" ? null : Number(v));
          }}
        />
        {field.minimum !== undefined && field.maximum !== undefined && (
          <p className="text-xs text-muted-foreground">范围: {field.minimum} — {field.maximum}</p>
        )}
      </div>
    );
  }

  if (field.type === "array") {
    return (
      <div className="space-y-1.5">
        <Label htmlFor={inputId}>{label}</Label>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
        <Input
          id={inputId}
          type="text"
          value={textValue}
          onChange={(e) => {
            const parts = e.target.value
              .split(",")
              .map((part) => part.trim())
              .filter(Boolean);
            if (field.items?.type === "integer" || field.items?.type === "number") {
              onChange(parts.map((part) => Number(part)).filter((part) => Number.isFinite(part)));
            } else {
              onChange(parts);
            }
          }}
          placeholder="用逗号分隔多个值"
        />
      </div>
    );
  }

  // 默认：string 类型
  const multiline =
    field.format === "textarea" ||
    field["x-ui-widget"] === "textarea" ||
    isTemplate ||
    textValue.includes("\n") ||
    defaultValue.includes("\n") ||
    /message|text|prompt|content/i.test(fk);

  if (multiline) {
    return (
      <div className="space-y-1.5">
        <Label htmlFor={inputId}>{label}</Label>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
        <Textarea
          id={inputId}
          value={textValue}
          rows={4}
          onChange={(e) => onChange(e.target.value)}
          placeholder={defaultValue}
        />
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId}>{label}</Label>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      <Input
        id={inputId}
        type={isSensitive ? "password" : "text"}
        value={textValue}
        onChange={(e) => onChange(e.target.value)}
        placeholder={isSensitive && !textValue ? MASKED_SECRET_PLACEHOLDER : defaultValue}
      />
    </div>
  );
}

function LLMProviderSelectField({
  inputId,
  label,
  description,
  value,
  providers,
  loading,
  onChange,
}: {
  inputId: string;
  label: string;
  description?: string;
  value: unknown;
  providers?: LLMProviderOut[];
  loading: boolean;
  onChange: (v: unknown) => void;
}) {
  const selected = formatConfigValue(value);
  const hasSelectedProvider = Boolean(selected) && Boolean(findLLMProviderBySelector(providers, selected));

  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId}>{label}</Label>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      <Select
        id={inputId}
        value={selected}
        disabled={loading}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{loading ? "正在加载 TelePilot Provider..." : "自动路由"}</option>
        {providers?.map((provider) => (
          <option key={provider.id} value={String(provider.id)}>
            {formatLLMProviderOptionLabel(provider)}
          </option>
        ))}
        {selected && !hasSelectedProvider && (
          <option value={selected}>当前值：{selected}</option>
        )}
      </Select>
      {!loading && (!providers || providers.length === 0) && (
        <p className="text-xs text-muted-foreground">
          尚未配置 TelePilot AI Provider；请先到 AI 模型提供商中添加。
        </p>
      )}
    </div>
  );
}

function LLMModelSelectField({
  inputId,
  label,
  description,
  value,
  providerValue,
  providers,
  loading,
  modelModality,
  onChange,
}: {
  inputId: string;
  label: string;
  description?: string;
  value: unknown;
  providerValue: unknown;
  providers?: LLMProviderOut[];
  loading: boolean;
  modelModality?: string;
  onChange: (v: unknown) => void;
}) {
  const selected = formatConfigValue(value);
  const provider = findLLMProviderBySelector(providers, formatConfigValue(providerValue));
  const rows = provider ? buildLLMModelOptions(provider, modelModality) : [];
  const hasSelectedModel = Boolean(selected) && rows.some((row) => row.value === selected);

  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId}>{label}</Label>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      <Select
        id={inputId}
        value={selected}
        disabled={loading || !provider}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">
          {provider ? "使用 Provider 默认模型" : loading ? "正在加载 TelePilot Provider..." : "跟随自动路由"}
        </option>
        {rows.map((row) => (
          <option key={row.value} value={row.value}>
            {row.label}
          </option>
        ))}
        {selected && !hasSelectedModel && (
          <option value={selected}>当前值：{selected}</option>
        )}
      </Select>
      {!loading && !provider && (
        <p className="text-xs text-muted-foreground">
          先选择固定 TelePilot Provider 后，才能选择该 Provider 下已启用的模型。
        </p>
      )}
    </div>
  );
}

export function schemaHasLLMSelect(schema: ConfigSchema | Record<string, unknown> | null): boolean {
  if (!schema || typeof schema !== "object" || !("properties" in schema)) return false;
  const properties = (schema as ConfigSchema).properties ?? {};
  return Object.values(properties).some((field) => {
    const widget = field?.["x-ui-widget"];
    return widget === "llm-provider-select" || widget === "llm-model-select";
  });
}

function findLLMProviderBySelector(providers: LLMProviderOut[] | undefined, selector: string): LLMProviderOut | null {
  const raw = selector.trim();
  if (!raw) return null;
  const lowered = raw.toLowerCase();
  return providers?.find((provider) => (
    String(provider.id) === raw ||
    provider.name.toLowerCase() === lowered ||
    String(provider.provider).toLowerCase() === lowered
  )) ?? null;
}

function formatLLMProviderOptionLabel(provider: LLMProviderOut): string {
  const tags = provider.tags && provider.tags.length > 0 ? ` · ${provider.tags.join(",")}` : "";
  const keyState = provider.has_api_key || provider.provider === "ollama" ? "" : " · 未配置 API Key";
  return `${provider.name}（${provider.provider} · ${provider.default_model}${tags}${keyState}）`;
}

function buildLLMModelOptions(provider: LLMProviderOut, modelModality?: string): Array<{ value: string; label: string }> {
  const enabled = (provider.models ?? []).filter((model) => (
    model.enabled && modelMatchesModality(model.id, modelModality)
  ));
  const seen = new Set<string>();
  const rows: Array<{ value: string; label: string }> = [];
  for (const model of enabled) {
    const value = model.id.trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    const label = model.label && model.label !== model.id ? `${model.label}（${model.id}）` : model.id;
    rows.push({ value, label });
  }
  return rows;
}

function modelMatchesModality(modelId: string, modelModality?: string): boolean {
  if ((modelModality || "").trim().toLowerCase() !== "text") return true;
  const normalized = modelId.trim().toLowerCase();
  return !(
    normalized.startsWith("gpt-image-") ||
    normalized.startsWith("dall-e-") ||
    normalized.includes("image")
  );
}

function isTemplateField(key: string): boolean {
  return key === "message_template" || /_template$/i.test(key);
}

function isPreviewField(key: string): boolean {
  return key === "template_preview" || /_preview$/i.test(key);
}

function isPlaceholderField(key: string): boolean {
  return key === "template_placeholders";
}

function isReadOnlyField(key: string, field: ConfigField): boolean {
  return Boolean(field.readOnly) || isPreviewField(key) || isPlaceholderField(key);
}

function renderPreviewValue(
  previewKey: string,
  previewField: ConfigField,
  fields: FieldEntry[],
  values: Record<string, unknown>,
  commandPrefix: string,
): string {
  const templateKey = findTemplateKeyForPreview(previewKey, fields);
  const templateField = templateKey ? fields.find(([key]) => key === templateKey)?.[1] : undefined;
  const templateValue = templateKey ? values[templateKey] : undefined;
  const template = formatConfigValue(templateValue ?? templateField?.default ?? previewField.default);
  return renderTemplateSample(normalizePreviewEscapes(template), values, commandPrefix);
}

function findTemplateKeyForPreview(previewKey: string, fields: FieldEntry[]): string | null {
  const keys = new Set(fields.map(([key]) => key));
  if (previewKey === "template_preview") {
    if (keys.has("round_message_template")) return "round_message_template";
    if (keys.has("message_template")) return "message_template";
  }

  const base = previewKey.replace(/_preview$/i, "");
  const candidates = [
    `${base}_message_template`,
    `${base}_template`,
    base,
  ];
  return candidates.find((key) => keys.has(key)) ?? null;
}

function renderTemplateSample(
  template: string,
  values: Record<string, unknown>,
  commandPrefix: string,
): string {
  const sample: Record<string, string> = {
    version: "1.0.0",
    prefix: commandPrefix || ",",
    command: formatConfigValue(values.command) || "dicegrid",
    force_stop_command: formatConfigValue(values.force_stop_command) || "stop",
    target_sum: "17",
    answer_index: "6",
    prize: "100",
    timeout: formatConfigValue(values.timeout) || "90",
    guess_cooldown: formatConfigValue(values.guess_cooldown) || "2.0",
    winner: "小明",
    elapsed: "8.2",
    example: "100",
    round: "12",
    number: "3",
    count: "5",
    cost: "50003",
    pool: "188888",
    winners: "2",
    payout: "53888",
    history_limit: formatConfigValue(values.history_show_limit) || "5",
    draw_numbers: formatConfigValue(values.draw_numbers) || "1,2,3,4,5,6",
    draw_time: `${padClockValue(values.draw_hour, "21")}:${padClockValue(values.draw_minute, "00")}`,
    close_minutes: formatConfigValue(values.close_minutes_before_draw) || "1",
    interval: formatConfigValue(values.auto_draw_interval_sec) || "86400",
    chat_id: "-1001234567890",
    chat_display: "示例工作群",
    summary: "1) 讨论了版本回滚原因\n2) 确认改为平台 AI 路由\n3) 约定今天内回归验证",
    time: "2026-05-26 14:30",
    message_count: formatConfigValue(values.default_count) || "100",
  };
  sample.title = "九宫格竞猜";
  sample.target_line = `目标点数：<b>${sample.target_sum}</b>（9 格里唯一）`;
  sample.guide_line = "回复 <code>1-9</code> 选择你认为答案所在的格子。";
  sample.reward_line = `首个答对者奖励：<b>+${sample.prize}</b> · 超时 ${sample.timeout} 秒`;

  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key: string) => sample[key] ?? match);
}

function normalizePreviewEscapes(value: string): string {
  return value
    .replace(/\\r\\n/g, "\n")
    .replace(/\\n/g, "\n")
    .replace(/\\r/g, "\n");
}

function padClockValue(value: unknown, fallback: string): string {
  const raw = formatConfigValue(value) || fallback;
  const num = Number(raw);
  if (!Number.isFinite(num)) return fallback;
  return String(Math.max(0, Math.floor(num))).padStart(2, "0");
}

function formatConfigValue(value: unknown): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ");
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

export function buildScopedConfigValues(
  schema: ConfigSchema,
  globalConfig: Record<string, unknown>,
  accountConfig: Record<string, unknown>,
): { globalVals: Record<string, unknown>; accountVals: Record<string, unknown> } {
  const globalVals: Record<string, unknown> = {};
  const accountVals: Record<string, unknown> = {};
  for (const [key, field] of Object.entries(schema.properties)) {
    const isGlobalField = field.level === "global";
    const effectiveVal = isGlobalField
      ? globalConfig[key] ?? accountConfig[key] ?? field.default
      : accountConfig[key] ?? globalConfig[key] ?? field.default;
    const displayVal = isSensitiveConfigKey(key) && isRedactedSecretValue(effectiveVal) ? "" : effectiveVal;
    if (isGlobalField) {
      globalVals[key] = displayVal;
    } else {
      accountVals[key] = displayVal;
    }
  }
  return { globalVals, accountVals };
}

export function withoutReadOnlyValues(
  values: Record<string, unknown>,
  properties: Record<string, ConfigField>,
  originalValues: Record<string, unknown> = {},
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(values)) {
    const field = properties[key];
    if (field && isReadOnlyField(key, field)) continue;
    if (isSensitiveConfigKey(key) && isRedactedSecretValue(originalValues[key]) && value === "") continue;
    if (isSensitiveConfigKey(key) && isRedactedSecretValue(value)) continue;
    out[key] = value;
  }
  return out;
}

function isSensitiveConfigKey(key: string): boolean {
  return /(^|_)(api_key|access_token|auth_token|bot_token|token|tokens|secret|password|passwd|pwd)$/i.test(key);
}

function isRedactedSecretValue(value: unknown): boolean {
  return typeof value === "string" && /^(\*{3,}|•{3,})$/.test(value.trim());
}

function ReadOnlyField({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div>
        <Label>{label}</Label>
        {description && <p className="mt-1 text-xs text-muted-foreground">{description}</p>}
      </div>
      <div className="rounded-md border bg-background px-3 py-2">
        {children}
      </div>
    </div>
  );
}
