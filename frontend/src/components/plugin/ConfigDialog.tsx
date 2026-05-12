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
import { toast } from "sonner";
import { TelegramHtmlPreview } from "@/components/TelegramHtmlPreview";
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

export interface ConfigField {
  key: string;
  title?: string;
  type: string;
  enum?: Array<string | number | boolean>;
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

  const handleSave = useCallback(async () => {
    if (!onSave) return;
    const properties = ((schema as ConfigSchema | null)?.properties ?? {}) as Record<string, ConfigField>;
    const editableGlobalVals = withoutReadOnlyValues(globalVals, properties);
    const editableAccountVals = withoutReadOnlyValues(accountVals, properties);
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
      const gv: Record<string, unknown> = {};
      const av: Record<string, unknown> = {};
      for (const [k, f] of Object.entries(s.properties)) {
        // 合并顺序：schema defaults < globalConfig < accountConfig
        const effectiveVal = accountConfig[k] ?? globalConfig[k] ?? f.default;
        if (f.level === "global") {
          gv[k] = effectiveVal;
        } else {
          av[k] = effectiveVal;
        }
      }
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
            <section className="space-y-3 rounded-md border bg-muted/30 p-3">
              <div>
                <div className="text-sm font-semibold">全局配置</div>
                <p className="text-xs text-muted-foreground">所有账号共享</p>
              </div>
              <div className="space-y-4">
                {globalFields.map(([key, field]) => (
                  <FieldInput key={key} fk={key} field={field} value={globalVals[key]} onChange={(v) => setGlobalVals(p => ({...p, [key]: v}))} />
                ))}
              </div>
            </section>
          )}
          {accountFields.length > 0 && (
            <section className="space-y-3 rounded-md border bg-muted/30 p-3">
              <div>
                <div className="text-sm font-semibold">账号配置</div>
                <p className="text-xs text-muted-foreground">{accountName ? accountName + " 专属" : "按账号隔离"}</p>
              </div>
              <div className="space-y-4">
                {accountFields.map(([key, field]) => (
                  <FieldInput key={key} fk={key} field={field} value={accountVals[key]} onChange={(v) => setAccountVals(p => ({...p, [key]: v}))} />
                ))}
              </div>
            </section>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? <><Spinner className="mr-2 h-4 w-4" /> 保存中…</> : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface FieldInputProps {
  fk: string;
  field: ConfigField;
  value: unknown;
  onChange: (v: unknown) => void;
}

function FieldInput({ fk, field, value, onChange }: FieldInputProps) {
  const label = field.title || fk;
  const description = field.description;
  const inputId = `plugin-config-${fk}`;
  const textValue = formatConfigValue(value);
  const defaultValue = field.default != null ? formatConfigValue(field.default) : "";
  const isPreview = isPreviewField(fk);
  const isPlaceholders = isPlaceholderField(fk);
  const isTemplate = isTemplateField(fk);
  const isReadOnly = isReadOnlyField(fk, field);

  if (isPreview) {
    return (
      <ReadOnlyField label={label} description={description}>
        <TelegramHtmlPreview value={textValue || defaultValue} mode="html" />
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

  if (field.enum && field.enum.length > 0) {
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
          {field.enum.map((opt) => (
            <option key={String(opt)} value={String(opt)}>
              {String(opt)}
            </option>
          ))}
        </Select>
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
  const multiline = isTemplate || textValue.includes("\n") || defaultValue.includes("\n") || /message|text|prompt|content/i.test(fk);

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
        type="text"
        value={textValue}
        onChange={(e) => onChange(e.target.value)}
        placeholder={defaultValue}
      />
    </div>
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

function formatConfigValue(value: unknown): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ");
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function withoutReadOnlyValues(
  values: Record<string, unknown>,
  properties: Record<string, ConfigField>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(values)) {
    const field = properties[key];
    if (field && isReadOnlyField(key, field)) continue;
    out[key] = value;
  }
  return out;
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
