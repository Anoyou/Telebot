import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Bell,
  Bot,
  ChevronRight,
  Code2,
  Copy,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Trash2,
  UserPlus,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/misc";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { SignalPill } from "@/components/ui/status";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  createAccountBotUser,
  deleteAccountBotUser,
  getAccountBot,
  getInteractionBotConfig,
  listAccountBotUsers,
  restartAccountBotRuntime,
  testAccountBot,
  updateAccountBot,
  updateAccountBotUser,
  updateInteractionBotConfig,
} from "@/api/accountBots";
import { getFeatureMatrix } from "@/api/features";
import type {
  AccountBotInteractionConfig,
  AccountBotInteractionConfigUpdate,
  AccountBotInteractionRule,
  FeatureInteractionEntry,
  AccountBotRemotePluginPolicy,
  AccountBotRole,
  AccountBotUserCreate,
} from "@/api/types";
import { getErrMsg } from "@/lib/api";
import { cn, formatDateTime } from "@/lib/utils";

const MASKED_SECRET_PLACEHOLDER = "••••••••••••••••";

function localizeBotRuntimeError(message: string): string {
  if (message.includes("terminated by other getUpdates request") || message.includes("Conflict:")) {
    return "交互 Bot polling 冲突：同一个 Bbot token 正在被另一个实例监听。请确认它没有被其他账号、本地/Docker/VPS 中的另一套 TelePilot，或其他程序同时使用。";
  }
  return message;
}

const ROLE_META: Record<AccountBotRole, { label: string; desc: string }> = {
  viewer: { label: "viewer", desc: "只读查看" },
  operator: { label: "operator", desc: "启停常用功能" },
  admin: { label: "admin", desc: "危险操作确认" },
};

const HELP_PREVIEW = `/start  打开主菜单
/status 查看账号、worker 与最近错误
/features 查看并启停账号功能
/commands 查看并启停自定义指令模板
/plugins 查看模块入口
/rules 查看规则，scheduler 规则可手动执行
/logs 查看最近运行日志
/pause /resume 暂停或恢复账号
/restart 重启账号 worker（admin + 二次确认）`;

const DEFAULT_REMOTE_POLICY: AccountBotRemotePluginPolicy = {
  enabled: false,
  install: false,
  update: false,
  uninstall: false,
  enable_disable: false,
};

const DEFAULT_INTERACTION_DISABLED_MESSAGE = "本条互动规则已暂停，暂时不能开启。";
const DEFAULT_INTERACTION_RESPONSE_TEMPLATE = "已收到 {payer_name} 给 {receiver_name} 的转账 {amount}，互动流程已准备就绪。";
const DEFAULT_INTERACTION_MODULE_START_TEXT = "正在启动互动模块...";
const DEFAULT_MATH10_START_KEYWORDS = "发十以内算数\n十以内算数\n开算数题";
const RULE_CONTROLLED_MODULE_CONFIG_KEYS = new Set(["prize", "timeout", "valid_seconds"]);
const DEFAULT_TRANSFER_NOTICE_TEMPLATE = [
  "转账成功",
  "付款人：{payer_name}",
  "{payer_user_id_line}",
  "收款人：{receiver_name}",
  "金额：{amount}",
  "{receiver_user_id_line}",
].join("\n");

const DEFAULT_INTERACTION_BOT: AccountBotInteractionConfig = {
  enabled: false,
  chat_id: null,
  chat_ids: [],
  interaction_bot_token: null,
  clear_interaction_bot_token: false,
  has_interaction_bot_token: false,
  interaction_bot_username: null,
  interaction_bot_id: null,
  interaction_running: false,
  interaction_runtime_status: "stopped",
  interaction_last_update_id: null,
  interaction_last_error: null,
  trusted_bot_id: null,
  transfer_bot_id: null,
  transfer_bot_token: null,
  clear_transfer_bot_token: false,
  has_transfer_bot_token: false,
  trigger_mode: "payment",
  trigger_text: "转账成功",
  trigger_texts: ["转账成功"],
  module_start_keywords: [],
  receiver_user_id: null,
  receiver_text: "",
  amount: null,
  amount_match_mode: "eq",
  action: "notice",
  math_prize: 123,
  module_key: null,
  module_action: null,
  module_config: {},
  module_prize: null,
  module_start_text: null,
  user_cooldown_seconds: null,
  daily_limit_per_user: null,
  open_commands: [],
  close_commands: [],
  status_commands: [],
  disabled_message: DEFAULT_INTERACTION_DISABLED_MESSAGE,
  valid_seconds: 600,
  concurrency: "chat",
  response_template: DEFAULT_INTERACTION_RESPONSE_TEMPLATE,
  transfer_notice_template: DEFAULT_TRANSFER_NOTICE_TEMPLATE,
  rules: [],
};

type InteractionRuleForm = {
  id: string;
  name: string;
  enabled: boolean;
  chatIds: string;
  triggerMode: NonNullable<AccountBotInteractionRule["trigger_mode"]>;
  triggerTexts: string;
  moduleStartKeywords: string;
  receiverUserId: string;
  receiverText: string;
  amount: string;
  amountMatchMode: NonNullable<AccountBotInteractionRule["amount_match_mode"]>;
  action: AccountBotInteractionRule["action"];
  mathPrize: string;
  moduleKey: string;
  moduleAction: string;
  moduleSessionScope: NonNullable<AccountBotInteractionRule["module_session_scope"]>;
  moduleConfig: string;
  moduleStartText: string;
  userCooldownSeconds: string;
  dailyLimitPerUser: string;
  openCommands: string;
  closeCommands: string;
  statusCommands: string;
  disabledMessage: string;
  validSeconds: string;
  concurrency: NonNullable<AccountBotInteractionRule["concurrency"]>;
  responseTemplate: string;
};

type InteractionEntryOption = {
  featureKey: string;
  featureName: string;
  entry: FeatureInteractionEntry;
  value: string;
  label: string;
};

type ResolvedInteractionEntry = InteractionEntryOption & {
  inferred: boolean;
};

function defaultRuleForm(index = 0): InteractionRuleForm {
  const suffix = index + 1;
  return {
    id: `rule-${Date.now()}-${suffix}`,
    name: suffix === 1 ? "默认规则" : `规则 ${suffix}`,
    enabled: true,
    chatIds: "",
    triggerMode: "payment",
    triggerTexts: DEFAULT_INTERACTION_BOT.trigger_text,
    moduleStartKeywords: "",
    receiverUserId: "",
    receiverText: "",
    amount: "",
    amountMatchMode: "eq",
    action: "notice",
    mathPrize: "123",
    moduleKey: "game24",
    moduleAction: "",
    moduleSessionScope: "chat",
    moduleConfig: "{}",
    moduleStartText: DEFAULT_INTERACTION_MODULE_START_TEXT,
    userCooldownSeconds: "",
    dailyLimitPerUser: "",
    openCommands: "",
    closeCommands: "",
    statusCommands: "",
    disabledMessage: DEFAULT_INTERACTION_BOT.disabled_message || "",
    validSeconds: "600",
    concurrency: "chat",
    responseTemplate: DEFAULT_INTERACTION_BOT.response_template,
  };
}

function parseOptionalInt(value: string, label: string): number | null {
  const text = value.trim();
  if (!text) return null;
  if (!/^-?\d+$/.test(text)) {
    throw new Error(`${label} 必须是整数`);
  }
  const parsed = Number(text);
  if (!Number.isSafeInteger(parsed)) {
    throw new Error(`${label} 超出安全整数范围`);
  }
  return parsed;
}

function parseOptionalPositiveInt(value: string, label: string): number | null {
  const parsed = parseOptionalInt(value, label);
  if (parsed == null) return null;
  if (parsed <= 0) {
    throw new Error(`${label} 必须大于 0`);
  }
  return parsed;
}

function parseOptionalUserId(value: string, label: string): number | null {
  return parseOptionalPositiveInt(value, label);
}

function parseIntLines(value: string, label: string): number[] {
  const lines = value
    .split(/[\n,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const out: number[] = [];
  for (const line of lines) {
    const parsed = parseOptionalInt(line, label);
    if (parsed == null) continue;
    if (!out.includes(parsed)) out.push(parsed);
  }
  return out;
}

function parseTextLines(value: string): string[] {
  const out: string[] = [];
  for (const line of value.split(/[\n,，]+/)) {
    const item = line.trim();
    if (item && !out.includes(item)) out.push(item);
  }
  return out;
}

function parseJsonObject(value: string, label: string): Record<string, unknown> {
  const text = value.trim();
  if (!text) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error(`${label} 必须是合法 JSON 对象`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} 必须是 JSON 对象`);
  }
  return parsed as Record<string, unknown>;
}

function formatJsonObject(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2);
}

function defaultModuleConfigFromEntry(entry?: FeatureInteractionEntry): Record<string, unknown> {
  const schema = entry?.input_schema;
  const properties = schema && typeof schema === "object" && !Array.isArray(schema)
    ? (schema as { properties?: unknown }).properties
    : null;
  if (!properties || typeof properties !== "object" || Array.isArray(properties)) {
    return {};
  }
  const config: Record<string, unknown> = {};
  for (const [key, rawField] of Object.entries(properties)) {
    if (RULE_CONTROLLED_MODULE_CONFIG_KEYS.has(key)) continue;
    if (!rawField || typeof rawField !== "object" || Array.isArray(rawField)) continue;
    if ("default" in rawField) {
      config[key] = (rawField as { default?: unknown }).default;
    }
  }
  return config;
}

function interactionEntryHasField(entry: FeatureInteractionEntry | undefined, key: string): boolean {
  const schema = entry?.input_schema;
  const properties = schema && typeof schema === "object" && !Array.isArray(schema)
    ? (schema as { properties?: unknown }).properties
    : null;
  return Boolean(properties && typeof properties === "object" && !Array.isArray(properties) && key in properties);
}

function stripRuleControlledModuleConfig(config: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(config).filter(([key]) => !RULE_CONTROLLED_MODULE_CONFIG_KEYS.has(key)),
  );
}

function defaultInteractionEntryForModule(
  entries: InteractionEntryOption[],
  moduleKey: string,
): InteractionEntryOption | undefined {
  const matches = entries.filter((item) => item.featureKey === moduleKey);
  return matches.length === 1 ? matches[0] : undefined;
}

function uniqueIntValues(values: number[]): number[] {
  const out: number[] = [];
  for (const value of values) {
    if (!out.includes(value)) out.push(value);
  }
  return out;
}

function countDelimitedTextItems(value: string): number {
  return new Set(
    value
      .split(/[\n,，\s]+/)
      .map((item) => item.trim())
      .filter(Boolean),
  ).size;
}

function getRuleActionLabel(action: AccountBotInteractionRule["action"]): string {
  if (action === "module") return "启动模块";
  if (action === "math10") return "算数题";
  return "只发通知";
}

function getRuleTriggerModeLabel(mode: NonNullable<AccountBotInteractionRule["trigger_mode"]>): string {
  if (mode === "keyword") return "仅关键词";
  if (mode === "both") return "转账或关键词";
  return "仅转账通知";
}

function getRuleConcurrencyLabel(mode: NonNullable<AccountBotInteractionRule["concurrency"]>): string {
  if (mode === "user") return "按用户";
  if (mode === "none") return "不并发";
  return "按群聊";
}

function resolveRuleModuleSelection(
  rule: InteractionRuleForm,
  interactionEntries: InteractionEntryOption[],
): ResolvedInteractionEntry | undefined {
  const explicit = interactionEntries.find((item) =>
    item.featureKey === rule.moduleKey
    && item.entry.key === rule.moduleAction,
  );
  if (explicit) {
    return { ...explicit, inferred: false };
  }
  const inferred = defaultInteractionEntryForModule(interactionEntries, rule.moduleKey || "game24");
  if (!inferred) return undefined;
  return {
    ...inferred,
    inferred: true,
  };
}

function describeRuleModuleSelection(
  rule: InteractionRuleForm,
  selection?: ResolvedInteractionEntry,
): string {
  if (!selection) {
    return rule.moduleKey.trim() ? `模块 ${rule.moduleKey} / 入口未选` : "模块入口未选";
  }
  const entryLabel = selection.entry.title || selection.entry.label || selection.entry.key;
  return selection.inferred || !rule.moduleAction.trim()
    ? `${selection.featureName} / 自动推断 ${selection.entry.key}`
    : `${selection.featureName} / ${entryLabel}`;
}

function ruleFormFromRule(
  rule: AccountBotInteractionRule,
  index: number,
  fallbackChatIds: number[] = [],
): InteractionRuleForm {
  const chatIds = rule.chat_ids?.length ? rule.chat_ids : fallbackChatIds;
  const prize = rule.action === "module" && rule.module_prize != null
    ? rule.module_prize
    : rule.math_prize || 123;
  return {
    id: rule.id || `rule-${index + 1}`,
    name: rule.name || `规则 ${index + 1}`,
    enabled: rule.enabled !== false,
    chatIds: chatIds.join("\n"),
    triggerMode: rule.trigger_mode || "payment",
    triggerTexts: rule.trigger_texts?.length
      ? rule.trigger_texts.join("\n")
      : DEFAULT_INTERACTION_BOT.trigger_text,
    moduleStartKeywords: rule.module_start_keywords?.join("\n") || "",
    receiverUserId: rule.receiver_user_id == null ? "" : String(rule.receiver_user_id),
    receiverText: rule.receiver_text ?? "",
    amount: rule.amount == null ? "" : String(rule.amount),
    amountMatchMode: rule.amount_match_mode || "eq",
    action: rule.action || "notice",
    mathPrize: String(prize),
    moduleKey: rule.module_key || "game24",
    moduleAction: rule.module_action || "",
    moduleSessionScope: rule.module_session_scope || "chat",
    moduleConfig: formatJsonObject(stripRuleControlledModuleConfig(rule.module_config ?? {})),
    moduleStartText: rule.module_start_text ?? "",
    userCooldownSeconds: rule.user_cooldown_seconds ?? "",
    dailyLimitPerUser: rule.daily_limit_per_user == null ? "" : String(rule.daily_limit_per_user),
    openCommands: rule.open_commands?.join("\n") || "",
    closeCommands: rule.close_commands?.join("\n") || "",
    statusCommands: rule.status_commands?.join("\n") || "",
    disabledMessage: rule.disabled_message || DEFAULT_INTERACTION_BOT.disabled_message || "",
    validSeconds: String(rule.valid_seconds || 600),
    concurrency: rule.concurrency || "chat",
    responseTemplate: rule.response_template || DEFAULT_INTERACTION_BOT.response_template,
  };
}

function legacyRuleFromConfig(config: AccountBotInteractionConfig): AccountBotInteractionRule {
  return {
    id: "legacy-default",
    name: "默认规则",
    enabled: true,
    chat_ids: config.chat_ids?.length
      ? config.chat_ids
      : config.chat_id == null
        ? []
        : [config.chat_id],
    trigger_texts: config.trigger_texts?.length
      ? config.trigger_texts
      : [config.trigger_text || DEFAULT_INTERACTION_BOT.trigger_text],
    trigger_mode: config.trigger_mode || "payment",
    module_start_keywords: config.module_start_keywords ?? [],
    receiver_user_id: config.receiver_user_id ?? null,
    receiver_text: config.receiver_text ?? null,
    amount: config.amount ?? null,
    amount_match_mode: config.amount_match_mode || "eq",
    action: config.action || "notice",
    math_prize: config.math_prize || 123,
    module_key: config.module_key ?? null,
    module_action: config.module_action ?? null,
    module_session_scope: config.module_session_scope ?? null,
    module_prize: config.module_prize ?? null,
    module_start_text: config.module_start_text ?? null,
    user_cooldown_seconds: config.user_cooldown_seconds ?? null,
    daily_limit_per_user: config.daily_limit_per_user ?? null,
    open_commands: config.open_commands ?? [],
    close_commands: config.close_commands ?? [],
    status_commands: config.status_commands ?? [],
    disabled_message: config.disabled_message ?? DEFAULT_INTERACTION_BOT.disabled_message,
    valid_seconds: config.valid_seconds ?? 600,
    concurrency: config.concurrency ?? "chat",
    response_template: config.response_template || DEFAULT_INTERACTION_BOT.response_template,
  };
}

function ruleFromForm(
  form: InteractionRuleForm,
  index: number,
  interactionEntries: InteractionEntryOption[] = [],
): AccountBotInteractionRule {
  const triggerTexts = parseTextLines(form.triggerTexts);
  const name = form.name.trim() || `规则 ${index + 1}`;
  const action = form.action === "module" ? "module" : form.action === "math10" ? "math10" : "notice";
  const triggerMode = action === "notice" ? "payment" : form.triggerMode;
  const savesPaymentFilters = triggerMode !== "keyword";
  const savesUserLimits = action === "module" && form.concurrency === "user";
  const mathPrize = parseOptionalPositiveInt(form.mathPrize, `${name} 奖金`) || 123;
  const moduleConfig = action === "module"
    ? stripRuleControlledModuleConfig(parseJsonObject(form.moduleConfig, `${name} 模块入口参数`))
    : {};
  const moduleKey = form.moduleKey.trim() || "game24";
  const moduleAction = form.moduleAction.trim()
    || defaultInteractionEntryForModule(interactionEntries, moduleKey)?.entry.key
    || "";
  const inferredEntry = interactionEntries.find((item) => item.featureKey === moduleKey && item.entry.key === moduleAction)?.entry;
  const moduleSessionScope = form.moduleSessionScope
    || (inferredEntry?.session_scope === "user" || inferredEntry?.session_scope === "none"
      ? inferredEntry.session_scope
      : "chat");
  return {
    id: form.id || `rule-${index + 1}`,
    name,
    enabled: form.enabled,
    chat_ids: parseIntLines(form.chatIds, `${name} Chat ID`),
    trigger_mode: triggerMode,
    trigger_texts: triggerTexts.length ? triggerTexts : [DEFAULT_INTERACTION_BOT.trigger_text],
    module_start_keywords: parseTextLines(form.moduleStartKeywords),
    receiver_user_id: savesPaymentFilters
      ? parseOptionalPositiveInt(form.receiverUserId, `${name} 指定收款人用户 ID`)
      : null,
    receiver_text: savesPaymentFilters ? form.receiverText.trim() || null : null,
    amount: savesPaymentFilters ? parseOptionalPositiveInt(form.amount, `${name} 金额过滤`) : null,
    amount_match_mode: form.amountMatchMode,
    action,
    math_prize: mathPrize,
    module_key: action === "module" ? moduleKey : null,
    module_action: action === "module" ? moduleAction || null : null,
    module_session_scope: action === "module" ? moduleSessionScope : null,
    module_config: moduleConfig,
    module_prize: action === "module"
      ? mathPrize
      : null,
    module_start_text: action === "module" ? form.moduleStartText.trim() || null : null,
    user_cooldown_seconds: savesUserLimits ? form.userCooldownSeconds.trim() || null : null,
    daily_limit_per_user: savesUserLimits
      ? parseOptionalPositiveInt(form.dailyLimitPerUser, `${name} 每用户每日上限`)
      : null,
    open_commands: parseTextLines(form.openCommands),
    close_commands: parseTextLines(form.closeCommands),
    status_commands: parseTextLines(form.statusCommands),
    disabled_message: form.disabledMessage.trim() || null,
    valid_seconds: parseOptionalPositiveInt(form.validSeconds, `${name} 参与有效期`) || 600,
    concurrency: action === "module" ? form.concurrency : "chat",
    response_template: form.responseTemplate.trim() || DEFAULT_INTERACTION_BOT.response_template,
  };
}

function InteractionRuleEditor({
  rule,
  index,
  ruleCount,
  interactionEntries,
  onPatch,
  onMove,
  onCopy,
  onRemove,
}: {
  rule: InteractionRuleForm;
  index: number;
  ruleCount: number;
  interactionEntries: InteractionEntryOption[];
  onPatch: (patch: Partial<InteractionRuleForm>) => void;
  onMove: (direction: -1 | 1) => void;
  onCopy: () => void;
  onRemove: () => void;
}) {
  const selectedModule = resolveRuleModuleSelection(rule, interactionEntries);
  const selectedInteractionEntry = selectedModule?.entry;
  const effectiveTriggerMode = rule.action === "notice" ? "payment" : rule.triggerMode;
  const showsPaymentFields = effectiveTriggerMode !== "keyword";
  const showsKeywordFields = effectiveTriggerMode !== "payment" && rule.action !== "notice";
  const hasPaidThreshold = rule.amount.trim().length > 0;
  const showsRuntimeSettings = rule.action !== "notice";
  const showsPrize = showsPaymentFields && (
    rule.action === "math10"
    || (rule.action === "module" && interactionEntryHasField(selectedInteractionEntry, "prize"))
  );
  const showsUserLimits = rule.action === "module" && rule.concurrency === "user";
  const moduleActionLabel = selectedModule
    ? selectedModule.entry.title || selectedModule.entry.label || selectedModule.entry.key
    : rule.moduleAction || "待选择";
  const moduleActionValue = rule.moduleAction.trim() || selectedModule?.entry.key || "";

  const updateAction = (value: string) => {
    const nextAction: InteractionRuleForm["action"] = value === "module"
      ? "module"
      : value === "math10"
        ? "math10"
        : "notice";
    const patch: Partial<InteractionRuleForm> = { action: nextAction };
    if (nextAction === "notice") {
      patch.triggerMode = "payment";
    }
    if (nextAction === "math10") {
      patch.triggerMode = rule.triggerMode === "payment" ? "both" : rule.triggerMode;
      patch.moduleStartKeywords = rule.moduleStartKeywords.trim()
        ? rule.moduleStartKeywords
        : DEFAULT_MATH10_START_KEYWORDS;
    }
    if (nextAction === "module" && !rule.moduleAction) {
      const entryOption = defaultInteractionEntryForModule(interactionEntries, rule.moduleKey || "game24");
      if (entryOption) {
        patch.moduleKey = entryOption.featureKey;
        patch.moduleAction = entryOption.entry.key;
        patch.moduleSessionScope = (entryOption.entry.session_scope === "user" || entryOption.entry.session_scope === "none"
          ? entryOption.entry.session_scope
          : "chat") as InteractionRuleForm["moduleSessionScope"];
        patch.moduleConfig = formatJsonObject(defaultModuleConfigFromEntry(entryOption.entry));
      }
    }
    onPatch(patch);
  };

  return (
    <div className="min-w-0 space-y-3 rounded-md border bg-background p-3 shadow-sm">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="grid min-w-0 flex-1 gap-3 sm:grid-cols-[minmax(0,1fr)_180px_96px]">
          <div className="space-y-1.5">
            <Label>规则名称</Label>
            <Input
              value={rule.name}
              onChange={(e) => onPatch({ name: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>命中后动作</Label>
            <Select
              value={rule.action}
              onChange={(e) => updateAction(e.target.value)}
            >
              <option value="notice">只发通知</option>
              <option value="math10">发十以内算数题</option>
              <option value="module">启动模块</option>
            </Select>
          </div>
          <label className="flex items-end justify-between gap-2 text-sm sm:pb-2">
            <span>启用</span>
            <Switch
              checked={rule.enabled}
              onCheckedChange={(checked) => onPatch({ enabled: checked })}
            />
          </label>
        </div>
        <div className="flex flex-wrap gap-2 lg:justify-end">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onMove(-1)}
            disabled={index === 0}
          >
            <ArrowUp className="mr-1 h-4 w-4" />
            上移
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onMove(1)}
            disabled={index === ruleCount - 1}
          >
            <ArrowDown className="mr-1 h-4 w-4" />
            下移
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={onCopy}>
            <Copy className="mr-1 h-4 w-4" />
            复制
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onRemove}
            disabled={ruleCount <= 1}
          >
            <Trash2 className="mr-1 h-4 w-4" />
            删除
          </Button>
        </div>
      </div>

      <div className="grid gap-3 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <section className="space-y-3 rounded-md border bg-muted/20 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-medium">触发条件</div>
            <Badge variant={rule.chatIds.trim() ? "secondary" : "destructive"}>
              {rule.chatIds.trim() ? `${countDelimitedTextItems(rule.chatIds)} 个群` : "缺少 Chat ID"}
            </Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1.1fr)_180px]">
            <div className="space-y-1.5">
              <Label>监听群 Chat ID</Label>
              <Textarea
                rows={3}
                placeholder={"-1001234567890\n-1009876543210"}
                value={rule.chatIds}
                onChange={(e) => onPatch({ chatIds: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>触发方式</Label>
              <Select
                value={effectiveTriggerMode}
                onChange={(e) => onPatch({ triggerMode: e.target.value as InteractionRuleForm["triggerMode"] })}
              >
                <option value="payment">仅转账通知</option>
                {rule.action !== "notice" ? (
                  <>
                    <option value="keyword">仅模块关键词</option>
                    <option value="both">转账或关键词</option>
                  </>
                ) : null}
              </Select>
            </div>
          </div>
          {showsPaymentFields ? (
            <div className="space-y-1.5">
              <Label>转账通知关键词</Label>
              <Textarea
                rows={3}
                placeholder={"转账成功\n交易成功"}
                value={rule.triggerTexts}
                onChange={(e) => onPatch({ triggerTexts: e.target.value })}
              />
            </div>
          ) : null}
          {rule.action === "module" && showsKeywordFields ? (
            <div className="space-y-1.5">
              <Label>模块启动文本/模板</Label>
              <Textarea
                rows={3}
                placeholder={"开24点\n置顶 id=数字\n猜骰 num=数字"}
                value={rule.moduleStartKeywords}
                onChange={(e) => onPatch({ moduleStartKeywords: e.target.value })}
              />
              <div className="text-xs text-muted-foreground">
                固定词直接写一行；需要提取数字时写 <code>id=数字</code> 或 <code>num=数字</code>。
              </div>
            </div>
          ) : null}
          {rule.action === "math10" && showsKeywordFields ? (
            <div className="space-y-1.5">
              <Label>算数题启动关键词</Label>
              <Textarea
                rows={3}
                placeholder={DEFAULT_MATH10_START_KEYWORDS}
                value={rule.moduleStartKeywords}
                onChange={(e) => onPatch({ moduleStartKeywords: e.target.value })}
              />
            </div>
          ) : null}
        </section>

        <section className="space-y-3 rounded-md border bg-muted/20 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-medium">动作与入口</div>
            <Badge variant={rule.enabled ? "secondary" : "outline"}>
              {rule.enabled ? getRuleActionLabel(rule.action) : "已暂停"}
            </Badge>
          </div>
          {rule.action === "notice" ? (
            <div className="space-y-1.5">
              <Label>通知模板</Label>
              <Textarea
                rows={5}
                placeholder={DEFAULT_INTERACTION_RESPONSE_TEMPLATE}
                value={rule.responseTemplate}
                onChange={(e) => onPatch({ responseTemplate: e.target.value })}
              />
            </div>
          ) : null}
          {rule.action === "math10" ? (
            <div className="rounded-md border bg-background px-3 py-2 text-sm text-muted-foreground">
              内置测试动作会在触发后发十以内算数题，适合先确认交互 Bot 的监听链路。
            </div>
          ) : null}
          {rule.action === "module" ? (
            <>
              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_160px]">
                <div className="space-y-1.5">
                  <Label>模块入口</Label>
                  <Select
                    value={selectedModule?.value ?? ""}
                    onChange={(e) => {
                      const selected = interactionEntries.find((item) => item.value === e.target.value);
                      if (!selected) return;
                      onPatch({
                        moduleKey: selected.featureKey,
                        moduleAction: selected.entry.key,
                        moduleSessionScope: (selected.entry.session_scope === "user" || selected.entry.session_scope === "none"
                          ? selected.entry.session_scope
                          : "chat") as InteractionRuleForm["moduleSessionScope"],
                        moduleConfig: formatJsonObject(defaultModuleConfigFromEntry(selected.entry)),
                      });
                    }}
                  >
                    {interactionEntries.length <= 0 ? (
                      <option value="">暂无声明交互入口的模块</option>
                    ) : null}
                    {interactionEntries.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label>会话范围</Label>
                  <Select
                    value={rule.moduleSessionScope}
                    onChange={(e) => onPatch({ moduleSessionScope: e.target.value as InteractionRuleForm["moduleSessionScope"] })}
                  >
                    <option value="chat">按群会话</option>
                    <option value="user">按用户会话</option>
                    <option value="none">不保存会话</option>
                  </Select>
                </div>
              </div>
              <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
                <div className="min-w-0 rounded-md border bg-background px-3 py-2">
                  <div className="mb-1 font-medium text-foreground">module_key</div>
                  <code className="block truncate">{rule.moduleKey || selectedModule?.featureKey || "未选择"}</code>
                </div>
                <div className="min-w-0 rounded-md border bg-background px-3 py-2">
                  <div className="mb-1 font-medium text-foreground">module_action</div>
                  <code className="block truncate">{moduleActionValue || "保存时尝试推断"}</code>
                  {!rule.moduleAction.trim() && selectedModule ? (
                    <div className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
                      当前由唯一入口自动推断：{selectedModule.entry.key}
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="grid gap-3 md:grid-cols-[160px_minmax(0,1fr)]">
                <div className="space-y-1.5">
                  <Label>并发策略</Label>
                  <Select
                    value={rule.concurrency}
                    onChange={(e) => onPatch({ concurrency: e.target.value as InteractionRuleForm["concurrency"] })}
                  >
                    <option value="chat">按群聊</option>
                    <option value="user">按用户</option>
                    <option value="none">不并发</option>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label>启动占位消息</Label>
                  <Input
                    placeholder={DEFAULT_INTERACTION_MODULE_START_TEXT}
                    value={rule.moduleStartText}
                    onChange={(e) => onPatch({ moduleStartText: e.target.value })}
                  />
                </div>
              </div>
              <div className="text-xs text-muted-foreground">
                当前入口：{moduleActionLabel}
              </div>
            </>
          ) : null}
        </section>
      </div>

      {showsPaymentFields ? (
        <section className="space-y-3 rounded-md border bg-muted/20 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-medium">付费与收款限制</div>
            <Badge variant={hasPaidThreshold ? "secondary" : "outline"}>
              {hasPaidThreshold ? `门槛 ${rule.amount}` : "不限制金额"}
            </Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <div className="space-y-1.5">
              <Label>付费参与门槛</Label>
              <Input
                inputMode="numeric"
                placeholder="留空表示任意金额通知都可触发"
                value={rule.amount}
                onChange={(e) => onPatch({ amount: e.target.value })}
              />
            </div>
            {hasPaidThreshold ? (
              <div className="space-y-1.5">
                <Label>金额匹配</Label>
                <Select
                  value={rule.amountMatchMode}
                  onChange={(e) => onPatch({ amountMatchMode: e.target.value as InteractionRuleForm["amountMatchMode"] })}
                >
                  <option value="eq">等于门槛</option>
                  <option value="gte">大于等于门槛</option>
                </Select>
              </div>
            ) : null}
            <div className="space-y-1.5">
              <Label>指定收款人用户 ID</Label>
              <Input
                inputMode="numeric"
                placeholder="留空时默认使用 userbot 本账户 ID"
                value={rule.receiverUserId}
                onChange={(e) => onPatch({ receiverUserId: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>指定收款人用户名/名称</Label>
              <Input
                placeholder="可填 @username；无 ID 时作为辅助匹配"
                value={rule.receiverText}
                onChange={(e) => onPatch({ receiverText: e.target.value })}
              />
            </div>
          </div>
        </section>
      ) : null}

      {showsRuntimeSettings ? (
        <section className="space-y-3 rounded-md border bg-muted/20 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-medium">运行与限流</div>
            <Badge variant="outline">
              有效期 {rule.validSeconds || "默认"} 秒
            </Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <div className="space-y-1.5">
              <Label>参与有效期（秒）</Label>
              <Input
                inputMode="numeric"
                value={rule.validSeconds}
                onChange={(e) => onPatch({ validSeconds: e.target.value })}
              />
            </div>
            {showsPrize ? (
              <div className="space-y-1.5">
                <Label>奖金</Label>
                <Input
                  inputMode="numeric"
                  value={rule.mathPrize}
                  onChange={(e) => onPatch({ mathPrize: e.target.value })}
                />
              </div>
            ) : null}
            {showsUserLimits ? (
              <>
                <div className="space-y-1.5">
                  <Label>每用户 CD</Label>
                  <Input
                    placeholder="例如 6h，留空不限制"
                    value={rule.userCooldownSeconds}
                    onChange={(e) => onPatch({ userCooldownSeconds: e.target.value })}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>每用户每日上限</Label>
                  <Input
                    inputMode="numeric"
                    placeholder="例如 2，留空不限制"
                    value={rule.dailyLimitPerUser}
                    onChange={(e) => onPatch({ dailyLimitPerUser: e.target.value })}
                  />
                </div>
              </>
            ) : null}
          </div>
        </section>
      ) : null}

      {rule.action === "module" ? (
        <details className="group rounded-md border bg-muted/20 px-3 py-2">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
            <span className="flex min-w-0 items-center gap-2">
              <Code2 className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="truncate">模块高级参数</span>
            </span>
            <span className="flex shrink-0 items-center gap-2 text-xs font-normal text-muted-foreground">
              通常不用填写
              <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90" />
            </span>
          </summary>
          <div className="mt-3 space-y-2">
            <Label>额外入口参数（JSON 对象）</Label>
            <Textarea
              rows={5}
              spellCheck={false}
              value={rule.moduleConfig}
              onChange={(e) => onPatch({ moduleConfig: e.target.value })}
            />
            <div className="text-xs text-muted-foreground">
              <code>prize</code>、<code>timeout</code> 和 <code>valid_seconds</code> 会由上方配置自动生成；只有插件入口声明了额外字段时才需要填写。
            </div>
          </div>
        </details>
      ) : null}

      <details className="group rounded-md border bg-muted/20 px-3 py-2">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
          <span>规则管理指令</span>
          <span className="flex shrink-0 items-center gap-2 text-xs font-normal text-muted-foreground">
            管理员临时开关时使用
            <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90" />
          </span>
        </summary>
        <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <div className="space-y-1.5">
            <Label>开启指令</Label>
            <Textarea
              rows={2}
              placeholder={"比如：\n开启24点\n打开游戏"}
              value={rule.openCommands}
              onChange={(e) => onPatch({ openCommands: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>关闭指令</Label>
            <Textarea
              rows={2}
              placeholder={"比如：\n关闭24点\n暂停游戏"}
              value={rule.closeCommands}
              onChange={(e) => onPatch({ closeCommands: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>状态指令</Label>
            <Textarea
              rows={2}
              placeholder="比如：24点状态"
              value={rule.statusCommands}
              onChange={(e) => onPatch({ statusCommands: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>关闭提示</Label>
            <Textarea
              rows={2}
              placeholder={DEFAULT_INTERACTION_DISABLED_MESSAGE}
              value={rule.disabledMessage}
              onChange={(e) => onPatch({ disabledMessage: e.target.value })}
            />
          </div>
        </div>
      </details>
    </div>
  );
}

export function BotTab({ aid }: { aid: number }) {
  const qc = useQueryClient();
  const [enabled, setEnabled] = useState(false);
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [remotePolicy, setRemotePolicy] = useState<AccountBotRemotePluginPolicy>(
    DEFAULT_REMOTE_POLICY,
  );
  const [transferEnabled, setTransferEnabled] = useState(false);
  const [interactionBotToken, setInteractionBotToken] = useState("");
  const [clearInteractionBotToken, setClearInteractionBotToken] = useState(false);
  const [transferBotId, setTransferBotId] = useState("");
  const [transferBotToken, setTransferBotToken] = useState("");
  const [clearTransferBotToken, setClearTransferBotToken] = useState(false);
  const [transferNoticeTemplate, setTransferNoticeTemplate] = useState(DEFAULT_TRANSFER_NOTICE_TEMPLATE);
  const [interactionRules, setInteractionRules] = useState<InteractionRuleForm[]>([
    defaultRuleForm(0),
  ]);
  const [selectedInteractionRuleId, setSelectedInteractionRuleId] = useState<string | null>(null);
  const [newUser, setNewUser] = useState<AccountBotUserCreate>({
    tg_user_id: 0,
    display_name: "",
    role: "viewer",
    notify_enabled: true,
    enabled: true,
  });

  const botQ = useQuery({
    queryKey: ["account", aid, "bot"],
    queryFn: () => getAccountBot(aid),
    enabled: !!aid,
  });
  const usersQ = useQuery({
    queryKey: ["account", aid, "bot", "users"],
    queryFn: () => listAccountBotUsers(aid),
    enabled: !!aid,
  });
  const interactionQ = useQuery({
    queryKey: ["account", aid, "interaction-bot"],
    queryFn: () => getInteractionBotConfig(aid),
    enabled: !!aid,
  });
  const matrixQ = useQuery({
    queryKey: ["feature-matrix"],
    queryFn: getFeatureMatrix,
  });

  const interactionEntries: InteractionEntryOption[] = (matrixQ.data?.features ?? []).flatMap((feature) =>
    (feature.interaction_entries ?? []).map((entry: FeatureInteractionEntry) => ({
      featureKey: feature.key,
      featureName: feature.display_name,
      entry,
      value: `${feature.key}:${entry.key}`,
      label: `${feature.display_name} / ${entry.title || entry.label || entry.key}`,
    })),
  );

  useEffect(() => {
    if (botQ.data) {
      setEnabled(botQ.data.enabled);
      setClearToken(false);
      setToken("");
      setRemotePolicy(botQ.data.remote_plugin_policy ?? DEFAULT_REMOTE_POLICY);
    }
  }, [botQ.data?.enabled, botQ.data?.has_token, botQ.data?.remote_plugin_policy]);

  useEffect(() => {
    if (interactionQ.data) {
      setTransferEnabled(interactionQ.data.enabled);
      setInteractionBotToken("");
      setClearInteractionBotToken(false);
      setTransferBotId(interactionQ.data.trusted_bot_id == null ? "" : String(interactionQ.data.trusted_bot_id));
      setTransferBotToken("");
      setClearTransferBotToken(false);
      setTransferNoticeTemplate(interactionQ.data.transfer_notice_template || DEFAULT_TRANSFER_NOTICE_TEMPLATE);
      const sourceRules = interactionQ.data.rules?.length
        ? interactionQ.data.rules
        : [legacyRuleFromConfig(interactionQ.data)];
      const fallbackChatIds = interactionQ.data.chat_ids?.length
        ? interactionQ.data.chat_ids
        : interactionQ.data.chat_id == null
          ? []
          : [interactionQ.data.chat_id];
      const nextRules = sourceRules.map((rule, index) => ruleFormFromRule(rule, index, fallbackChatIds));
      setInteractionRules(nextRules);
      setSelectedInteractionRuleId((current) =>
        nextRules.some((rule) => rule.id === current)
          ? current
          : nextRules[0]?.id ?? null,
      );
    }
  }, [interactionQ.data]);

  useEffect(() => {
    setSelectedInteractionRuleId((current) =>
      interactionRules.some((rule) => rule.id === current)
        ? current
        : interactionRules[0]?.id ?? null,
    );
  }, [interactionRules]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["account", aid, "bot"] });
    qc.invalidateQueries({ queryKey: ["account", aid, "bot", "users"] });
    qc.invalidateQueries({ queryKey: ["account", aid, "interaction-bot"] });
  };

  const saveMut = useMutation({
    mutationFn: () =>
      updateAccountBot(aid, {
        enabled,
        clear_token: clearToken,
        bot_token: token.trim() || null,
        remote_plugin_policy: remotePolicy,
      }),
    onSuccess: () => {
      toast.success("Bot 配置已保存");
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const testMut = useMutation({
    mutationFn: () => testAccountBot(aid),
    onSuccess: () => toast.success("测试消息已发送"),
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const restartMut = useMutation({
    mutationFn: () => restartAccountBotRuntime(aid),
    onSuccess: () => {
      toast.success("Bot polling runtime 已重启");
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const buildInteractionPayload = (overrides?: Partial<AccountBotInteractionConfigUpdate>): AccountBotInteractionConfigUpdate => {
    const existingInteractionBotToken = Boolean(interactionQ.data?.has_interaction_bot_token) && !clearInteractionBotToken;
    const nextInteractionBotToken = interactionBotToken.trim();
    const nextTransferBotToken = transferBotToken.trim();
    const rules = interactionRules.map((rule, index) => ruleFromForm(rule, index, interactionEntries));
    const chatIds = uniqueIntValues(rules.flatMap((rule) => rule.chat_ids ?? []));
    if (rules.length <= 0) {
      throw new Error("至少需要保留一条规则");
    }
    const firstRule = rules.find((rule) => rule.enabled) ?? rules[0];
    if (transferEnabled && !existingInteractionBotToken && !nextInteractionBotToken) {
      throw new Error("启用转账联动时必须填写交互 Bot Token");
    }
    if (transferEnabled && chatIds.length <= 0) {
      throw new Error("启用转账联动时至少需要在一条规则里填写监听群 Chat ID");
    }
    return {
      enabled: transferEnabled,
      chat_id: chatIds[0] ?? null,
      chat_ids: chatIds,
      interaction_bot_token: nextInteractionBotToken || null,
      clear_interaction_bot_token: clearInteractionBotToken,
      trusted_bot_id: parseOptionalUserId(transferBotId, "转账结果通知 Bot 用户 ID"),
      transfer_bot_token: nextTransferBotToken || null,
      clear_transfer_bot_token: clearTransferBotToken,
      trigger_mode: firstRule.trigger_mode,
      trigger_text: firstRule.trigger_texts?.[0] || DEFAULT_INTERACTION_BOT.trigger_text,
      trigger_texts: firstRule.trigger_texts?.length ? firstRule.trigger_texts : DEFAULT_INTERACTION_BOT.trigger_texts,
      module_start_keywords: firstRule.module_start_keywords ?? [],
      receiver_user_id: firstRule.receiver_user_id ?? null,
      receiver_text: firstRule.receiver_text ?? null,
      amount: firstRule.amount ?? null,
      amount_match_mode: firstRule.amount_match_mode,
      action: firstRule.action,
      math_prize: firstRule.math_prize || 123,
      module_key: firstRule.module_key ?? null,
      module_action: firstRule.module_action ?? null,
      module_config: firstRule.module_config ?? {},
      module_prize: firstRule.module_prize ?? null,
      module_start_text: firstRule.module_start_text ?? null,
      user_cooldown_seconds: firstRule.user_cooldown_seconds ?? null,
      daily_limit_per_user: firstRule.daily_limit_per_user ?? null,
      open_commands: firstRule.open_commands ?? [],
      close_commands: firstRule.close_commands ?? [],
      status_commands: firstRule.status_commands ?? [],
      disabled_message: firstRule.disabled_message ?? null,
      valid_seconds: firstRule.valid_seconds ?? 600,
      concurrency: firstRule.concurrency ?? "chat",
      response_template: firstRule.response_template || DEFAULT_INTERACTION_BOT.response_template,
      transfer_notice_template: transferNoticeTemplate.trim() || DEFAULT_TRANSFER_NOTICE_TEMPLATE,
      rules,
      ...overrides,
    };
  };

  const saveTransferMut = useMutation({
    mutationFn: () => updateInteractionBotConfig(aid, buildInteractionPayload()),
    onSuccess: () => {
      toast.success("交互 Bot 配置已保存");
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const saveInteractionBotMut = useMutation({
    mutationFn: () => updateInteractionBotConfig(aid, buildInteractionPayload()),
    onSuccess: () => {
      toast.success("交互 Bot 身份配置已保存");
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const clearInteractionBotMut = useMutation({
    mutationFn: () => updateInteractionBotConfig(aid, buildInteractionPayload({
      interaction_bot_token: null,
      clear_interaction_bot_token: true,
    })),
    onSuccess: () => {
      toast.success("交互 Bot Token 已清空");
      setInteractionBotToken("");
      setClearInteractionBotToken(false);
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const saveTransferResultBotMut = useMutation({
    mutationFn: () => updateInteractionBotConfig(aid, buildInteractionPayload()),
    onSuccess: () => {
      toast.success("转账结果通知 Bot 配置已保存");
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const clearTransferResultBotMut = useMutation({
    mutationFn: () => updateInteractionBotConfig(aid, buildInteractionPayload({
      trusted_bot_id: null,
      transfer_bot_token: null,
      clear_transfer_bot_token: true,
    })),
    onSuccess: () => {
      toast.success("转账结果通知 Bot 配置已清空");
      setTransferBotId("");
      setTransferBotToken("");
      setClearTransferBotToken(false);
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const updateInteractionRule = (
    index: number,
    patch: Partial<InteractionRuleForm>,
  ) => {
    setInteractionRules((rules) =>
      rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)),
    );
  };

  const addInteractionRule = () => {
    setInteractionRules((rules) => {
      const nextRule = defaultRuleForm(rules.length);
      setSelectedInteractionRuleId(nextRule.id);
      return [...rules, nextRule];
    });
  };

  const copyInteractionRule = (index: number) => {
    setInteractionRules((rules) => {
      const source = rules[index];
      if (!source) return rules;
      const nextRule = {
        ...source,
        id: `rule-${Date.now()}-${index + 2}`,
        name: `${source.name || `规则 ${index + 1}`} 副本`,
      };
      const next = [...rules];
      next.splice(index + 1, 0, nextRule);
      setSelectedInteractionRuleId(nextRule.id);
      return next;
    });
  };

  const removeInteractionRule = (index: number) => {
    setInteractionRules((rules) => {
      if (rules.length <= 1) {
        toast.error("至少需要保留一条规则");
        return rules;
      }
      const removedRule = rules[index];
      const next = rules.filter((_, i) => i !== index);
      if (removedRule?.id === selectedInteractionRuleId) {
        setSelectedInteractionRuleId(next[Math.min(index, next.length - 1)]?.id ?? null);
      }
      return next;
    });
  };

  const moveInteractionRule = (index: number, direction: -1 | 1) => {
    setInteractionRules((rules) => {
      const nextIndex = index + direction;
      if (nextIndex < 0 || nextIndex >= rules.length) return rules;
      const next = [...rules];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
  };

  const addUserMut = useMutation({
    mutationFn: () => {
      if (!newUser.tg_user_id) throw new Error("请填写 Telegram 用户 ID");
      return createAccountBotUser(aid, {
        ...newUser,
        display_name: newUser.display_name?.trim() || null,
      });
    },
    onSuccess: () => {
      toast.success("授权用户已添加");
      setNewUser({
        tg_user_id: 0,
        display_name: "",
        role: "viewer",
        notify_enabled: true,
        enabled: true,
      });
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const updateUserMut = useMutation({
    mutationFn: (vars: { uid: number; patch: Partial<AccountBotUserCreate> }) =>
      updateAccountBotUser(aid, vars.uid, vars.patch),
    onSuccess: () => invalidate(),
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteUserMut = useMutation({
    mutationFn: (uid: number) => deleteAccountBotUser(aid, uid),
    onSuccess: () => {
      toast.success("授权用户已删除");
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (botQ.isLoading || usersQ.isLoading || interactionQ.isLoading) {
    return (
      <div className="flex h-28 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  const bot = botQ.data;
  const users = usersQ.data ?? [];
  const hasInteractionToken = Boolean(interactionQ.data?.has_interaction_bot_token) && !clearInteractionBotToken;
  const hasTransferToken = Boolean(interactionQ.data?.has_transfer_bot_token) && !clearTransferBotToken;
  const hasRuleChatIds = interactionRules.some((rule) => rule.chatIds.trim());
  const interactionReady = hasRuleChatIds && (hasInteractionToken || Boolean(interactionBotToken.trim()));
  const interactionRunning = Boolean(interactionQ.data?.interaction_running);
  const transferReady =
    hasRuleChatIds
    && (hasInteractionToken || Boolean(interactionBotToken.trim()));
  const selectedInteractionRuleIndex = interactionRules.findIndex((rule) => rule.id === selectedInteractionRuleId);
  const selectedInteractionRuleIndexSafe = selectedInteractionRuleIndex >= 0 ? selectedInteractionRuleIndex : 0;
  const selectedInteractionRule = interactionRules[selectedInteractionRuleIndexSafe];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-2">
        <SignalPill tone={bot.enabled ? "success" : "warn"} label="管理 Bot" value={bot.enabled ? "已启用" : "未启用"} />
        <SignalPill tone={interactionReady ? "primary" : "neutral"} label="互动规则" value={interactionReady ? "可执行" : "待配置"} />
        <SignalPill tone={interactionRunning ? "success" : "neutral"} label="互动运行态" value={interactionRunning ? "运行中" : "未运行"} />
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Bot className="h-4 w-4" /> 管理 Bot 配置
              <Badge variant="destructive" className="ml-1">
                危险操作需 Telegram 内二次确认
              </Badge>
            </CardTitle>
            <CardDescription>
              管理 Bot 能通过 Telegram Bot 远程管理你账号。每个账号绑定一个普通 Bot，互相隔离授权和通知。Bot Token 不会回显。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              安全提示：涉及重启、安装等危险操作时，需在 Telegram 内完成二次确认后才会执行。
            </div>
            <div className="space-y-3 rounded-md border border-red-300/70 bg-red-50 px-3 py-3 dark:border-red-400/40 dark:bg-red-950/30">
              <div className="flex items-center gap-2">
                <div className="text-sm font-medium text-red-900 dark:text-red-100">远程模块高风险开关（admin）</div>
                <Dialog>
                  <DialogTrigger asChild>
                    <button
                      type="button"
                      className="text-xs font-medium text-red-700 underline underline-offset-2 hover:text-red-800 dark:text-red-200 dark:hover:text-red-100"
                    >
                      详情
                    </button>
                  </DialogTrigger>
                  <DialogContent className="max-h-[85vh] w-[calc(100vw-2rem)] max-w-xl overflow-y-auto rounded-xl">
                    <DialogHeader>
                      <DialogTitle className="text-base">远程模块高风险开关说明</DialogTitle>
                      <DialogDescription>
                        这是管理 Bot 里的高风险远程模块总闸，采用“Web 策略开关 + Telegram 内二次确认”双重防护。
                      </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-3 text-sm text-foreground/90">
                      <div><strong>总开关 enabled：</strong>关闭时所有高风险远程模块动作都不允许从 TG Bot 发起；开启后才会继续检查子开关。</div>
                      <div><strong>允许 install：</strong>控制 <code>/plugins install &lt;git-url&gt;</code>。</div>
                      <div><strong>允许 update：</strong>控制 <code>/plugins update &lt;name&gt;</code>。</div>
                      <div><strong>允许 uninstall：</strong>控制 <code>/plugins uninstall &lt;name&gt;</code>。</div>
                      <div><strong>允许第三方 enable/disable：</strong>控制第三方远程模块启停操作。</div>
                      <div><strong>权限边界：</strong>仅 <code>admin</code> 可操作；<code>viewer/operator</code> 会被拦截。</div>
                      <div><strong>执行机制：</strong>开关只代表“允许发起请求”，真正执行前仍需在 Telegram 内二次确认，且确认有时效并绑定发起人。</div>
                    </div>
                  </DialogContent>
                </Dialog>
              </div>
              <div className="text-xs text-red-800 dark:text-red-200">
                默认全部关闭；即使开启后，Telegram 内仍需二次确认才会执行 install/update/uninstall/第三方启停。
              </div>
              <div className="grid gap-2 text-sm md:grid-cols-2">
                {[
                  ["enabled", "总开关"],
                  ["install", "允许 install"],
                  ["update", "允许 update"],
                  ["uninstall", "允许 uninstall"],
                  ["enable_disable", "允许第三方 enable/disable"],
                ].map(([key, label]) => (
                  <label
                    key={key}
                    className="flex items-center justify-between rounded border border-red-200/70 bg-white/95 px-3 py-2 text-red-950 dark:border-red-300/30 dark:bg-red-950/10 dark:text-red-100"
                  >
                    <span>{label}</span>
                    <Switch
                      checked={remotePolicy[key as keyof AccountBotRemotePluginPolicy]}
                      onCheckedChange={(checked) =>
                        setRemotePolicy((prev) => ({
                          ...prev,
                          [key]: checked,
                        }))
                      }
                    />
                  </label>
                ))}
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>运行状态</Label>
                <div className="flex h-10 items-center gap-2 rounded-md border px-3 text-sm">
                  <Badge variant={bot?.enabled ? "default" : "secondary"}>
                    {bot?.enabled ? "已启用" : "未启用"}
                  </Badge>
                  <span className="font-mono text-muted-foreground">
                    {bot?.status ?? "disabled"}
                  </span>
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>Bot 用户名（@开头的）</Label>
                <div className="flex h-10 items-center rounded-md border px-3 text-sm">
                  {bot?.username ? `@${bot.username}` : "保存 token 后自动读取"}
                </div>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="account-bot-token">Bot Token</Label>
              <div className="flex gap-2">
                <Input
                  id="account-bot-token"
                  type="password"
                  autoComplete="off"
                  placeholder={bot?.has_token ? MASKED_SECRET_PLACEHOLDER : "123456:ABC-DEF..."}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                />
                <Button
                  type="button"
                  variant={clearToken ? "destructive" : "outline"}
                  onClick={() => setClearToken((v) => !v)}
                >
                  <KeyRound className="mr-1 h-4 w-4" />
                  {clearToken ? "将清空" : "清空"}
                </Button>
              </div>
            </div>

            {bot?.last_error ? (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                {localizeBotRuntimeError(bot.last_error)}
              </div>
            ) : null}

            <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={enabled} onCheckedChange={setEnabled} />
                启用 管理 Bot
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={() => testMut.mutate()}
                  disabled={testMut.isPending || !bot?.has_token}
                >
                  {testMut.isPending ? (
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="mr-1 h-4 w-4" />
                  )}
                  测试发送
                </Button>
                <Button
                  variant="outline"
                  onClick={() => restartMut.mutate()}
                  disabled={restartMut.isPending}
                >
                  <RefreshCw className="mr-1 h-4 w-4" />
                  重启 runtime
                </Button>
                <Button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
                  {saveMut.isPending ? (
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  ) : null}
                  保存管理 Bot
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheck className="h-4 w-4" /> 可操作范围
            </CardTitle>
            <CardDescription>GUI 是完整控制台，Bot 覆盖高频远程操作。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="grid gap-2">
              {Object.entries(ROLE_META).map(([role, meta]) => (
                <div key={role} className="flex items-center justify-between rounded-md border px-3 py-2">
                  <span className="font-mono">{meta.label}</span>
                  <span className="text-muted-foreground">{meta.desc}</span>
                </div>
              ))}
            </div>
            <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 text-xs leading-5">
              {HELP_PREVIEW}
            </pre>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Bot className="h-4 w-4" /> 交互 Bot / 转账结果通知 Bot 的娱乐性联动（Beta）

          </CardTitle>
          <CardDescription>
            为了减少娱乐模块的高频率 API 调用会对人形 Bot 产生封号的风险，特有此方案。<br />
            通过给人形 Bot 发特定格式消息, 实现娱乐功能。<br />
            交互 Bot 能帮你独立监听指定群里的 “+数字“这类消息，然后帮你互动；转账结果通知 Bot 可用于发模拟转账通知。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge variant={transferEnabled ? "default" : "secondary"}>
                {transferEnabled ? "联动已启用" : "联动未启用"}
              </Badge>
              <Badge variant={interactionReady ? "secondary" : "destructive"}>
                {interactionReady ? "交互 Bot 可监听" : "缺少监听配置"}
              </Badge>
              <Badge variant={interactionRunning ? "default" : "secondary"}>
                {interactionRunning ? "运行中" : "未运行"}
              </Badge>
              <Badge variant={hasTransferToken || transferBotToken.trim() ? "secondary" : "outline"}>
                {hasTransferToken || transferBotToken.trim() ? "测试通知 Bot 已配置" : "测试通知 Bot 可选"}
              </Badge>
              {interactionQ.data?.interaction_last_update_id != null ? (
                <Badge variant="outline">
                  update #{interactionQ.data.interaction_last_update_id}
                </Badge>
              ) : null}
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={transferEnabled} onCheckedChange={setTransferEnabled} />
              启用
            </label>
          </div>

          {interactionQ.data?.interaction_last_error ? (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              {localizeBotRuntimeError(interactionQ.data.interaction_last_error)}
            </div>
          ) : null}

          <div className="rounded-md border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-medium">监听群来源</div>
                <div className="text-xs text-muted-foreground">
                  每条规则独立填写 Chat ID；保存时会自动汇总到旧版顶层监听群字段。
                </div>
              </div>
              <Badge variant={hasRuleChatIds ? "secondary" : "destructive"}>
                {hasRuleChatIds ? "在规则内填写" : "至少一条规则必填"}
              </Badge>
            </div>
          </div>

          <div className="space-y-3 rounded-md border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-medium">交互 Bot</div>
                <div className="text-xs text-muted-foreground">
                  用户 ID 会从 Token 自动读取，不需要手填；Telegram Bot API 发消息和收更新都只需要 Token。
                </div>
              </div>
              <Badge variant={hasInteractionToken || interactionBotToken.trim() ? "secondary" : "destructive"}>
                {hasInteractionToken || interactionBotToken.trim() ? "已配置" : "缺少 Token"}
              </Badge>
            </div>
            <div className="grid gap-3 md:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
              <div className="space-y-1.5">
                <Label>用户名（@开头的）</Label>
                <div className="flex h-10 items-center rounded-md border px-3 text-sm">
                  {interactionQ.data?.interaction_bot_username
                    ? `@${interactionQ.data.interaction_bot_username}`
                    : "保存 Token 后自动读取"}
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>Token</Label>
                <Input
                  type="password"
                  autoComplete="off"
                  placeholder={interactionQ.data?.has_interaction_bot_token ? MASKED_SECRET_PLACEHOLDER : "123456:ABC-DEF..."}
                  value={interactionBotToken}
                  onChange={(e) => setInteractionBotToken(e.target.value)}
                />
              </div>
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => saveInteractionBotMut.mutate()}
                disabled={saveInteractionBotMut.isPending || !interactionQ.data}
              >
                {saveInteractionBotMut.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
                保存交互 Bot
              </Button>
              <Button
                type="button"
                variant="destructive"
                onClick={() => clearInteractionBotMut.mutate()}
                disabled={clearInteractionBotMut.isPending || !interactionQ.data}
              >
                <KeyRound className="mr-1 h-4 w-4" />
                清空交互 Bot
              </Button>
            </div>
          </div>

          <div className="space-y-3 rounded-md border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-medium">转账结果通知 Bot</div>
                <div className="text-xs text-muted-foreground">
                  测试环境才需要填写 Token；正式群里已有官方转账通知 Bot 时，只填写 官方转账 Bot 的用户 ID（一串数字） 作为信任来源即可。
                </div>
              </div>
              <Badge variant={hasTransferToken || transferBotToken.trim() || transferBotId.trim() ? "secondary" : "outline"}>
                可选
              </Badge>
            </div>
            <div className="grid gap-3 md:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
              <div className="space-y-1.5">
                <Label>用户 ID（一串数字，不是@开头的）</Label>
                <Input
                  inputMode="numeric"
                  placeholder="可选；填写后只信任该通知 Bot"
                  value={transferBotId}
                  onChange={(e) => setTransferBotId(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Token</Label>
                <Input
                  type="password"
                  autoComplete="off"
                  placeholder={interactionQ.data?.has_transfer_bot_token ? MASKED_SECRET_PLACEHOLDER : "测试模拟通知时填写"}
                  value={transferBotToken}
                  onChange={(e) => setTransferBotToken(e.target.value)}
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>测试通知模板</Label>
              <Textarea
                rows={5}
                placeholder={DEFAULT_TRANSFER_NOTICE_TEMPLATE}
                value={transferNoticeTemplate}
                onChange={(e) => setTransferNoticeTemplate(e.target.value)}
              />
              <div className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
                <span><code>{"{payer_name}"}</code>：付款人显示名</span>
                <span><code>{"{payer_user_id}"}</code>：付款人用户 ID</span>
                <span><code>{"{receiver_name}"}</code>：收款人显示名</span>
                <span><code>{"{amount}"}</code>：转账金额</span>
                <span><code>{"{receiver_user_id}"}</code>：收款人用户 ID</span>
                <span className="sm:col-span-2"><code>{"{payer_user_id_line}"}</code>：有付款人 ID 时渲染为“付款人ID：数字”，没有时自动留空</span>
                <span className="sm:col-span-2"><code>{"{receiver_user_id_line}"}</code>：有收款人 ID 时渲染为“收款人ID：数字”，没有时自动留空</span>
              </div>
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => saveTransferResultBotMut.mutate()}
                disabled={saveTransferResultBotMut.isPending || !interactionQ.data}
              >
                {saveTransferResultBotMut.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
                保存通知 Bot
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => clearTransferResultBotMut.mutate()}
                disabled={clearTransferResultBotMut.isPending || !interactionQ.data}
              >
                <KeyRound className="mr-1 h-4 w-4" />
                清空通知 Bot
              </Button>
            </div>
          </div>

          <div className="space-y-3 rounded-md border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-medium">规则工作台（{interactionRules.length} 条）</div>
                <div className="text-xs text-muted-foreground">
                  左边看摘要和顺序，右边只编辑当前选中的一条。module_action 留空时会按唯一入口自动推断。
                </div>
              </div>
              <Button type="button" variant="outline" onClick={addInteractionRule}>
                <Plus className="mr-1 h-4 w-4" />
                新增规则
              </Button>
            </div>

            <div className="grid gap-3 xl:grid-cols-[320px_minmax(0,1fr)]">
              <div className="space-y-2 rounded-md border bg-muted/20 p-2 xl:max-h-[72vh] xl:overflow-y-auto">
                {interactionRules.map((rule, index) => {
                  const resolvedModule = resolveRuleModuleSelection(rule, interactionEntries);
                  const effectiveTriggerMode = rule.action === "notice" ? "payment" : rule.triggerMode;
                  const isSelected = rule.id === selectedInteractionRule?.id;
                  return (
                    <div
                      key={rule.id}
                      className={cn(
                        "rounded-md border bg-background p-2 transition-colors",
                        isSelected ? "border-primary/40 bg-primary/5 shadow-sm" : "border-border/70",
                      )}
                    >
                      <button
                        type="button"
                        className="flex w-full items-start gap-3 text-left"
                        onClick={() => setSelectedInteractionRuleId(rule.id)}
                      >
                        <div className={cn(
                          "grid h-7 w-7 shrink-0 place-items-center rounded-md border text-xs font-semibold",
                          isSelected ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-muted/40 text-muted-foreground",
                        )}>
                          {index + 1}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <div className="truncate text-sm font-medium">{rule.name || `规则 ${index + 1}`}</div>
                              <div className="mt-1 flex flex-wrap gap-1.5">
                                <Badge variant={rule.enabled ? "secondary" : "outline"} className="h-6 px-2">
                                  {rule.enabled ? "启用" : "暂停"}
                                </Badge>
                                <Badge
                                  variant={rule.action === "module" ? "default" : rule.action === "math10" ? "secondary" : "outline"}
                                  className="h-6 px-2"
                                >
                                  {getRuleActionLabel(rule.action)}
                                </Badge>
                                <Badge variant={effectiveTriggerMode === "payment" ? "secondary" : "outline"} className="h-6 px-2">
                                  {getRuleTriggerModeLabel(effectiveTriggerMode)}
                                </Badge>
                              </div>
                            </div>
                          </div>
                          <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                            <div className="truncate">
                              {rule.chatIds.trim() ? `${countDelimitedTextItems(rule.chatIds)} 个群 · ${rule.action === "notice" ? "通知" : rule.action === "module" ? "模块" : "算数题"}` : "未填写监听群"}
                            </div>
                            <div className="truncate">
                              {rule.action === "module"
                                ? describeRuleModuleSelection(rule, resolvedModule)
                                : rule.action === "notice"
                                  ? "只发通知"
                                  : `关键词 ${countDelimitedTextItems(rule.moduleStartKeywords)} 条`}
                            </div>
                          </div>
                        </div>
                      </button>

                      <div className="mt-2 flex flex-wrap justify-end gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          title="上移"
                          aria-label="上移规则"
                          onClick={() => moveInteractionRule(index, -1)}
                          disabled={index === 0}
                        >
                          <ArrowUp className="h-4 w-4" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          title="下移"
                          aria-label="下移规则"
                          onClick={() => moveInteractionRule(index, 1)}
                          disabled={index === interactionRules.length - 1}
                        >
                          <ArrowDown className="h-4 w-4" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          title="复制"
                          aria-label="复制规则"
                          onClick={() => copyInteractionRule(index)}
                        >
                          <Copy className="h-4 w-4" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          title="删除"
                          aria-label="删除规则"
                          onClick={() => removeInteractionRule(index)}
                          disabled={interactionRules.length <= 1}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="min-w-0">
                {selectedInteractionRule ? (
                  <InteractionRuleEditor
                    rule={selectedInteractionRule}
                    index={selectedInteractionRuleIndexSafe}
                    ruleCount={interactionRules.length}
                    interactionEntries={interactionEntries}
                    onPatch={(patch) => updateInteractionRule(selectedInteractionRuleIndexSafe, patch)}
                    onMove={(direction) => moveInteractionRule(selectedInteractionRuleIndexSafe, direction)}
                    onCopy={() => copyInteractionRule(selectedInteractionRuleIndexSafe)}
                    onRemove={() => removeInteractionRule(selectedInteractionRuleIndexSafe)}
                  />
                ) : (
                  <div className="rounded-md border bg-muted/20 p-6 text-sm text-muted-foreground">
                    当前没有可编辑的规则。
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
            群里回复任意消息发送 <code>+123</code> 后，若已填写转账结果通知 Bot Token，会生成：转账成功 / 发送者 射出 123 / 被回复者 接收 123。
            没有测试用的转账通知结果 Bot 的 Token 时，交互 Bot 只监听群里真实出现的转账结果通知。
          </div>
          <div className="flex justify-end">
            <Button
              variant="outline"
              onClick={() => saveTransferMut.mutate()}
              disabled={saveTransferMut.isPending || !interactionQ.data || (transferEnabled && !transferReady)}
            >
              {saveTransferMut.isPending ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              ) : null}
              保存整块交互配置
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Bell className="h-4 w-4" /> 管理 Bot 授权用户
          </CardTitle>
          <CardDescription>
            未授权用户默认无响应。授权用户发 /start 后会记录 last_chat_id，用于通知和测试发送。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 rounded-md border p-3 md:grid-cols-[180px_minmax(0,1fr)_150px_120px]">
            <div className="space-y-1.5">
              <Label>Telegram 用户 ID</Label>
              <Input
                inputMode="numeric"
                placeholder="123456789"
                value={newUser.tg_user_id || ""}
                onChange={(e) =>
                  setNewUser((v) => ({ ...v, tg_user_id: Number(e.target.value) || 0 }))
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label>备注名</Label>
              <Input
                placeholder="例如：我 / 运维同事"
                value={newUser.display_name ?? ""}
                onChange={(e) => setNewUser((v) => ({ ...v, display_name: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label>角色</Label>
              <Select
                value={newUser.role}
                onChange={(e) =>
                  setNewUser((v) => ({ ...v, role: e.target.value as AccountBotRole }))
                }
              >
                {Object.keys(ROLE_META).map((role) => (
                  <option key={role} value={role}>
                    {role}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex items-end">
              <Button
                className="w-full"
                onClick={() => addUserMut.mutate()}
                disabled={addUserMut.isPending}
              >
                <UserPlus className="mr-1 h-4 w-4" />
                添加
              </Button>
            </div>
          </div>

          <Table className="table-fixed">
            <colgroup>
              <col className="w-[22%]" />
              <col className="w-[18%]" />
              <col className="w-[16%]" />
              <col className="w-[12%]" />
              <col className="w-[14%]" />
              <col className="w-[18%]" />
            </colgroup>
            <TableHeader>
              <TableRow>
                <TableHead>用户</TableHead>
                <TableHead>备注</TableHead>
                <TableHead>角色</TableHead>
                <TableHead className="text-center">通知</TableHead>
                <TableHead className="text-center">启用</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.id}>
                  <TableCell>
                    <div className="font-mono">{u.tg_user_id}</div>
                    <div className="text-xs text-muted-foreground">
                      chat: {u.last_chat_id ?? "未记录"}
                    </div>
                  </TableCell>
                  <TableCell>
                    <Input
                      defaultValue={u.display_name ?? ""}
                      placeholder="无"
                      onBlur={(e) => {
                        if (e.target.value === (u.display_name ?? "")) return;
                        updateUserMut.mutate({
                          uid: u.id,
                          patch: { display_name: e.target.value || null },
                        });
                      }}
                    />
                  </TableCell>
                  <TableCell>
                    <Select
                      value={u.role}
                      onChange={(e) =>
                        updateUserMut.mutate({
                          uid: u.id,
                          patch: { role: e.target.value as AccountBotRole },
                        })
                      }
                    >
                      {Object.keys(ROLE_META).map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </Select>
                  </TableCell>
                  <TableCell className="text-center">
                    <Switch
                      checked={u.notify_enabled}
                      onCheckedChange={(v) =>
                        updateUserMut.mutate({ uid: u.id, patch: { notify_enabled: v } })
                      }
                    />
                  </TableCell>
                  <TableCell className="text-center">
                    <Switch
                      checked={u.enabled}
                      onCheckedChange={(v) =>
                        updateUserMut.mutate({ uid: u.id, patch: { enabled: v } })
                      }
                    />
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <span className="hidden text-xs text-muted-foreground xl:inline">
                        {formatDateTime(u.created_at)}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-destructive"
                        onClick={() => deleteUserMut.mutate(u.id)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {users.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="h-20 text-center text-muted-foreground">
                    还没有授权用户
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
