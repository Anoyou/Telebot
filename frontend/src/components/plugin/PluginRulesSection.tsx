// 通用规则区（x-ui-mode=rules）：规则列表 + 表单编辑 + 试运行卡片，全部由插件声明驱动。
// 取代 AutoReply / Autorepeat 专属页（对照用的旧页面在 pages/Plugins/configs/legacy，下一版删除）。
import { useMemo, useRef, useState } from "react";
import { Pencil, Play, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import type { RuleDryRunResponse, RuleOut } from "@/api/types";
import { DryRunDetail } from "@/components/DryRunDetail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { LLMProviderOut } from "@/api/types";
import { ConfigScopeSection, type ConfigField } from "@/components/plugin/ConfigDialog";
import { orderedPropertyEntries } from "@/components/plugin/configFieldOrder";
import {
  dryRunSpec,
  formatRuleValue,
  missingRequiredFields,
  parseChatId,
  ruleDefaults,
  ruleFormValues,
  ruleSummaryKeys,
  type PluginRulesLayout,
} from "@/lib/pluginRules";
import { useRuleCrud } from "@/pages/Plugins/configs/_shared/useRuleCrud";
import { RuleEditDialogShell } from "@/pages/Plugins/configs/_shared/RuleEditDialogShell";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/feedback/EmptyState";

interface FormState {
  name: string;
  enabled: boolean;
  priority: number;
  config: Record<string, unknown>;
}

export interface PluginRulesSectionProps {
  aid: number;
  featureKey: string;
  schema: unknown;
  layout: PluginRulesLayout;
  commandPrefix: string;
  llmProviders?: LLMProviderOut[];
  llmProvidersLoading?: boolean;
}

const CHAT_TYPE_OPTIONS: Array<{ value: "private" | "group" | "channel"; label: string }> = [
  { value: "private", label: "私聊" },
  { value: "group", label: "群聊" },
  { value: "channel", label: "频道" },
];

export function PluginRulesSection({
  aid,
  featureKey,
  schema,
  layout,
  commandPrefix,
  llmProviders,
  llmProvidersLoading,
}: PluginRulesSectionProps) {
  const crud = useRuleCrud({ aid, ruleKind: featureKey });
  const ruleSchema = layout.ruleSchema;
  const entries = useMemo(
    () => orderedPropertyEntries(ruleSchema.properties ?? {}, ruleSchema["x-ui-property-order"]) as unknown as Array<[string, ConfigField]>,
    [ruleSchema],
  );
  const fieldByKey = useMemo(() => new Map(entries), [entries]);
  const summaryKeys = useMemo(
    () => ruleSummaryKeys(schema, layout, entries.map(([key]) => key)),
    [schema, layout, entries],
  );
  const dry = dryRunSpec(schema);
  const rules = crud.rulesQ.data ?? [];

  // ── 编辑 / 新建 ──
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<RuleOut | null>(null);
  const [form, setForm] = useState<FormState>({ name: "", enabled: true, priority: 100, config: {} });

  function openCreate() {
    setEditing(null);
    setForm({ name: "", enabled: true, priority: 100, config: ruleDefaults(ruleSchema) });
    setEditOpen(true);
  }
  function openEdit(rule: RuleOut) {
    setEditing(rule);
    setForm({
      name: rule.name,
      enabled: rule.enabled,
      priority: rule.priority,
      config: ruleFormValues(ruleSchema, rule.config as Record<string, unknown>),
    });
    setEditOpen(true);
  }
  async function handleSave() {
    if (!form.name.trim()) {
      toast.error("请填写规则名称");
      return;
    }
    const missing = missingRequiredFields(ruleSchema, form.config);
    if (missing.length > 0) {
      toast.error(`请填写：${missing.join("、")}`);
      return;
    }
    await crud.saveRule({
      editing,
      payload: { name: form.name.trim(), enabled: form.enabled, priority: form.priority, config: form.config },
      onSuccess: () => setEditOpen(false),
    });
  }

  // ── 试运行卡片 ──
  const dryCardRef = useRef<HTMLDivElement | null>(null);
  const [dryRuleId, setDryRuleId] = useState<number | null>(null);
  const [drySample, setDrySample] = useState("");
  const [dryChatType, setDryChatType] = useState<"private" | "group" | "channel">("private");
  const [dryChatId, setDryChatId] = useState("");
  const [dryResult, setDryResult] = useState<RuleDryRunResponse | null>(null);
  const dryRule = rules.find((rule) => rule.id === dryRuleId) ?? rules[0] ?? null;

  function selectDryRun(rule: RuleOut) {
    setDryRuleId(rule.id);
    setDryResult(null);
    dryCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function runDry() {
    if (!dryRule) return;
    const chatId = dry.sampleFields.includes("chat_id") ? parseChatId(dryChatId) : null;
    crud.dryRun({
      rid: dryRule.id,
      payload: {
        sample_message: drySample,
        ...(dry.sampleFields.includes("chat_type") ? { sample_chat_type: dryChatType } : {}),
        ...(chatId != null ? { sample_chat_id: chatId } : {}),
      },
      onSuccess: setDryResult,
    });
  }

  return (
    <>
      <Card data-testid="plugin-rules">
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <CardTitle className="text-base">规则</CardTitle>
              <CardDescription>每条规则单独生效，按优先级从高到低匹配；保存后由 worker 热加载。</CardDescription>
            </div>
            <Button onClick={openCreate} data-testid="plugin-rules-create">
              <Plus className="mr-1 h-4 w-4" /> 新建规则
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {crud.rulesQ.isLoading ? (
            <div className="flex h-20 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : rules.length === 0 ? (
            <EmptyState title="还没有规则" description="点「新建规则」添加第一条。" />
          ) : (
            <ul className="space-y-2" data-testid="plugin-rules-list">
              {rules.map((rule) => {
                const cfg = (rule.config ?? {}) as Record<string, unknown>;
                return (
                  <li
                    key={rule.id}
                    className="flex flex-col gap-2 rounded-md border bg-card px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between"
                    data-testid="plugin-rule-row"
                  >
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{rule.name}</span>
                        <Badge variant={rule.enabled ? "success" : "secondary"}>{rule.enabled ? "ON" : "OFF"}</Badge>
                        <span className="text-xs text-muted-foreground">优先级 {rule.priority}</span>
                      </div>
                      {summaryKeys.length > 0 ? (
                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                          {summaryKeys.map((key) => (
                            <span key={key} className="min-w-0 break-all">
                              {fieldByKey.get(key)?.title || key}：
                              <span className="text-foreground">{formatRuleValue(fieldByKey.get(key) as never, cfg[key])}</span>
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 flex-wrap gap-1">
                      <Button size="sm" variant="ghost" onClick={() => openEdit(rule)}>
                        <Pencil className="mr-1 h-3.5 w-3.5" /> 编辑
                      </Button>
                      {dry.enabled ? (
                        <Button size="sm" variant="ghost" onClick={() => selectDryRun(rule)}>
                          <Play className="mr-1 h-3.5 w-3.5" /> 试运行
                        </Button>
                      ) : null}
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-destructive"
                        onClick={async () => {
                          if (await confirmDialog({ title: `删除规则「${rule.name}」？`, description: "删除后不可恢复。", confirmLabel: "删除", tone: "danger" })) crud.removeRule(rule.id);
                        }}
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" /> 删除
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {dry.enabled && rules.length > 0 ? (
        <div ref={dryCardRef} className="scroll-mt-4">
          <Card data-testid="plugin-rules-dry-run">
            <CardHeader>
              <CardTitle className="text-base">{dry.title}</CardTitle>
              <CardDescription>{dry.description}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <label className="block space-y-1">
                <span className="text-xs font-medium">规则</span>
                <Select
                  value={dryRule ? String(dryRule.id) : ""}
                  onChange={(event) => {
                    setDryRuleId(Number(event.target.value));
                    setDryResult(null);
                  }}
                  aria-label="试运行的规则"
                >
                  {rules.map((rule) => (
                    <option key={rule.id} value={rule.id}>{rule.name}</option>
                  ))}
                </Select>
              </label>
              {dry.sampleFields.includes("message") ? (
                <label className="block space-y-1">
                  <span className="text-xs font-medium">样例消息</span>
                  <Textarea
                    rows={2}
                    value={drySample}
                    onChange={(event) => setDrySample(event.target.value)}
                    placeholder="输入要测试的文本内容"
                    aria-label="样例消息"
                  />
                </label>
              ) : null}
              <div className="grid gap-3 sm:grid-cols-2">
                {dry.sampleFields.includes("chat_type") ? (
                  <label className="block space-y-1">
                    <span className="text-xs font-medium">会话类型</span>
                    <Select
                      value={dryChatType}
                      onChange={(event) => setDryChatType(event.target.value as "private" | "group" | "channel")}
                      aria-label="会话类型"
                    >
                      {CHAT_TYPE_OPTIONS.map((item) => (
                        <option key={item.value} value={item.value}>{item.label}</option>
                      ))}
                    </Select>
                  </label>
                ) : null}
                {dry.sampleFields.includes("chat_id") ? (
                  <label className="block space-y-1">
                    <span className="text-xs font-medium">样本会话 ID（可选）</span>
                    <Input
                      inputMode="numeric"
                      value={dryChatId}
                      onChange={(event) => setDryChatId(event.target.value.replace(/[^0-9-]/g, ""))}
                      placeholder="例：-1001234567890"
                      aria-label="样本会话 ID"
                    />
                  </label>
                ) : null}
              </div>
              <Button
                onClick={runDry}
                disabled={!dryRule || crud.dryRunPending || (dry.sampleFields.includes("message") && !drySample.trim())}
                data-testid="plugin-rules-dry-run-submit"
              >
                <Play className="mr-1 h-4 w-4" /> {crud.dryRunPending ? "运行中…" : "运行"}
              </Button>
              {dryResult ? (
                <div className="space-y-2" data-testid="plugin-rules-dry-run-result">
                  <div className="rounded-md border bg-muted/40 p-3 text-xs">
                    <div className="mb-1">
                      命中：
                      <Badge variant={dryResult.matched ? "success" : "secondary"}>{dryResult.matched ? "是" : "否"}</Badge>
                    </div>
                    {dryResult.output != null ? <pre className="whitespace-pre-wrap break-all">{dryResult.output}</pre> : null}
                  </div>
                  <DryRunDetail detail={dryResult.detail} />
                </div>
              ) : null}
            </CardContent>
          </Card>
        </div>
      ) : null}

      <RuleEditDialogShell
        open={editOpen}
        onOpenChange={setEditOpen}
        editing={editing}
        description="字段由插件声明；保存后立即生效。"
        name={form.name}
        enabled={form.enabled}
        priority={form.priority}
        onNameChange={(name) => setForm((prev) => ({ ...prev, name }))}
        onEnabledChange={(enabled) => setForm((prev) => ({ ...prev, enabled }))}
        onPriorityChange={(priority) => setForm((prev) => ({ ...prev, priority }))}
        onSave={() => void handleSave()}
        saving={crud.saving}
      >
        {entries.length > 0 ? (
          <ConfigScopeSection
            title="规则内容"
            description="按插件声明的字段填写。"
            fields={entries}
            values={form.config}
            accountId={aid}
            commandPrefix={commandPrefix}
            llmProviders={llmProviders}
            llmProvidersLoading={llmProvidersLoading}
            showPreviews={false}
            onChange={(key, value) => setForm((prev) => ({ ...prev, config: { ...prev.config, [key]: value } }))}
            onChangeMany={(patch) => setForm((prev) => ({ ...prev, config: { ...prev.config, ...patch } }))}
          />
        ) : (
          <p className="text-xs text-muted-foreground">插件没有声明规则字段。</p>
        )}
      </RuleEditDialogShell>
    </>
  );
}
