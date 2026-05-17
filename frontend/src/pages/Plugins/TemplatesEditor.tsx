// 系统设置 → 自定义命令模板（4 种类型：reply_text / forward_to / run_plugin / ai）
//
// 设计：
//   列表页：全表展示模板，name 徽章 type，编辑/删除按钮
//   编辑对话框：根据 type 切不同子表单
//   保存后后端会通知所有启用此模板的 worker 热加载
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Trash2, Edit3, Search } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { OutputTemplateEditor } from "@/components/ai/OutputTemplateEditor";
import { Spinner } from "@/components/ui/misc";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import {
  createCommandTemplate,
  deleteCommandTemplate,
  listBuiltinCommands,
  listCommandTemplates,
  listLLMProviders,
  patchCommandTemplate,
} from "@/api/commands";
import { getSystemSettings } from "@/api/system";
import type {
  CommandTemplateOut,
  CommandTemplateType,
  LLMProviderOut,
} from "@/api/types";
import { getErrMsg } from "@/lib/api";

// 命令指令仅允许 [a-zA-Z0-9_]，与后端正则对齐
const NAME_RE = /^[a-zA-Z0-9_]{1,64}$/;
const ALIAS_RE = /^[a-zA-Z0-9_]{1,16}$/;

const TYPE_LABELS: Record<CommandTemplateType, string> = {
  reply_text: "回复文本",
  forward_to: "转发到",
  run_plugin: "调插件",
  ai: "AI",
};

interface FormState {
  id?: number;
  name: string;
  type: CommandTemplateType;
  description: string;
  aliases_text: string;
  // 各 type 的 config 字段散开存，按 type 切表单时拼回
  text: string;
  target_chat_id: string;
  /** forward_to：触发命令后多少秒删命令消息（空 / 0 = 不删） */
  forward_delete_after: string;
  /** forward_to：成功后立即删除命令消息（不等待） */
  forward_delete_immediately: boolean;
  /** forward_to：转发方式（forward_native/copy_text/quote/link_only） */
  forward_mode: string;
  plugin_key: string;
  plugin_method: string;
  plugin_args: string; // JSON string
  ai_provider_id: string; // <select> value，转 number 后下发
  ai_model: string;
  ai_system_prompt: string;
  ai_max_tokens: string;
  ai_quote_replied: boolean;
  // ── 路由（auto 模式才用到，fixed 留空即可）──
  ai_routing_mode: "fixed" | "auto";
  ai_routing_fallback_provider_id: string;  // <select> value
  ai_classifier_provider_id: string;        // <select> value，可空
  // ── 输出格式（消息编辑回 TG 时长什么样）──
  ai_output_format: "html" | "markdown" | "plain";
  ai_output_template: string;
  ai_escape_values: boolean;
  ai_web_search: boolean;
  ai_web_search_context_size: "low" | "medium" | "high";
  // ── 发送方式 ──
  // edit:    原地编辑 ,ai 命令消息（默认；保留 reply 链）
  // send_new: 删命令、发新消息（不带 reply_to）——避免在被回复方那里留下"你回复了我"痕迹
  ai_send_mode: "edit" | "send_new";
}

const EMPTY_FORM: FormState = {
  name: "",
  type: "reply_text",
  description: "",
  aliases_text: "",
  text: "",
  target_chat_id: "",
  forward_delete_after: "",
  forward_delete_immediately: false,
  forward_mode: "forward_native",
  plugin_key: "",
  plugin_method: "",
  plugin_args: "[]",
  ai_provider_id: "",
  ai_model: "",
  ai_system_prompt: "你是简洁有用的中文助手。回答控制在 100 字内。",
  ai_max_tokens: "512",
  ai_quote_replied: true,
  ai_routing_mode: "fixed",
  ai_routing_fallback_provider_id: "",
  ai_classifier_provider_id: "",
  ai_output_format: "html",
  ai_output_template: "",
  ai_escape_values: true,
  ai_web_search: false,
  ai_web_search_context_size: "medium",
  ai_send_mode: "edit",
};

function formFromTemplate(t: CommandTemplateOut): FormState {
  const cfg = t.config || {};
  return {
    id: t.id,
    name: t.name,
    type: t.type,
    description: t.description || "",
    aliases_text: (t.aliases || []).join(", "),
    text: typeof cfg.text === "string" ? (cfg.text as string) : "",
    target_chat_id:
      cfg.target_chat_id !== undefined && cfg.target_chat_id !== null
        ? String(cfg.target_chat_id)
        : "",
    forward_delete_after:
      cfg.delete_after !== undefined && cfg.delete_after !== null
        ? String(cfg.delete_after)
        : "",
    forward_delete_immediately: !!cfg.delete_immediately,
    forward_mode: typeof cfg.mode === "string" ? (cfg.mode as string) : "forward_native",
    plugin_key: typeof cfg.plugin_key === "string" ? (cfg.plugin_key as string) : "",
    plugin_method: typeof cfg.method === "string" ? (cfg.method as string) : "",
    plugin_args: cfg.args ? JSON.stringify(cfg.args) : "[]",
    ai_provider_id:
      cfg.provider_id !== undefined && cfg.provider_id !== null
        ? String(cfg.provider_id)
        : "",
    ai_model: typeof cfg.model === "string" ? (cfg.model as string) : "",
    ai_system_prompt:
      typeof cfg.system_prompt === "string"
        ? (cfg.system_prompt as string)
        : EMPTY_FORM.ai_system_prompt,
    ai_max_tokens:
      cfg.max_tokens !== undefined && cfg.max_tokens !== null
        ? String(cfg.max_tokens)
        : "512",
    ai_quote_replied: cfg.quote_replied !== false, // 默认 true
    ai_routing_mode:
      cfg.routing_mode === "auto" ? "auto" : "fixed",
    ai_routing_fallback_provider_id:
      cfg.routing_fallback_provider_id !== undefined &&
        cfg.routing_fallback_provider_id !== null
        ? String(cfg.routing_fallback_provider_id)
        : "",
    ai_classifier_provider_id:
      cfg.classifier_provider_id !== undefined &&
        cfg.classifier_provider_id !== null
        ? String(cfg.classifier_provider_id)
        : "",
    ai_output_format:
      cfg.output_format === "html" ||
        cfg.output_format === "markdown" ||
        cfg.output_format === "plain"
        ? cfg.output_format
        : "html", // 老 'markdownv2' / 缺省 → 默认 html
    ai_output_template: typeof cfg.output_template === "string" ? cfg.output_template : "",
    ai_escape_values: cfg.escape_values !== false,
    ai_web_search: cfg.web_search === true,
    ai_web_search_context_size:
      cfg.web_search_context_size === "low" ||
        cfg.web_search_context_size === "medium" ||
        cfg.web_search_context_size === "high"
        ? cfg.web_search_context_size
        : "medium",
    ai_send_mode: cfg.send_mode === "send_new" ? "send_new" : "edit",
  };
}

// 根据 type 拼出 config 对象 + 入参校验
function buildPayload(form: FormState): {
  ok: boolean;
  errMsg?: string;
  config?: Record<string, unknown>;
  aliases?: string[];
} {
  const aliases = form.aliases_text
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  for (const alias of aliases) {
    if (!ALIAS_RE.test(alias)) {
      return { ok: false, errMsg: `别名不合法：${alias}` };
    }
  }
  const t = form.type;
  if (t === "reply_text") {
    return { ok: true, aliases, config: { text: form.text } };
  }
  if (t === "forward_to") {
    const cfg: Record<string, unknown> = {};
    const v = form.target_chat_id.trim();
    if (v) {
      const n = Number(v);
      if (!Number.isInteger(n)) return { ok: false, errMsg: "target_chat_id 必须是整数" };
      cfg.target_chat_id = n;
    }
    if (form.forward_mode && form.forward_mode !== "forward_native") {
      cfg.mode = form.forward_mode;
    }
    if (form.forward_delete_immediately) {
      cfg.delete_immediately = true;
    } else {
      const da = form.forward_delete_after.trim();
      if (da) {
        const n = Number(da);
        if (!Number.isInteger(n) || n < 0 || n > 3600) {
          return { ok: false, errMsg: "自动删除秒数必须是 0~3600 的整数" };
        }
        if (n > 0) cfg.delete_after = n;
      }
    }
    return { ok: true, aliases, config: cfg };
  }
  if (t === "run_plugin") {
    if (!form.plugin_key.trim())
      return { ok: false, errMsg: "plugin_key 必填" };
    let args: unknown = [];
    try {
      args = form.plugin_args ? JSON.parse(form.plugin_args) : [];
    } catch {
      return { ok: false, errMsg: "args 不是合法 JSON" };
    }
    return {
      ok: true, aliases,
      config: {
        plugin_key: form.plugin_key.trim(),
        method: form.plugin_method.trim() || undefined,
        args,
      },
    };
  }
  // ai
  const pid = Number(form.ai_provider_id);
  if (!Number.isInteger(pid) || pid <= 0)
    return { ok: false, errMsg: "AI 类型必须选择模型提供商" };
  const mt = form.ai_max_tokens.trim();
  const cfg: Record<string, unknown> = {
    provider_id: pid,
    quote_replied: form.ai_quote_replied,
    system_prompt: form.ai_system_prompt,
    routing_mode: form.ai_routing_mode,
  };
  if (form.ai_model.trim()) cfg.model = form.ai_model.trim();
  if (mt) cfg.max_tokens = Number(mt) || 512;
  // 路由字段：只在 auto 模式下下发，避免 fixed 留脏数据
  if (form.ai_routing_mode === "auto") {
    if (form.ai_routing_fallback_provider_id.trim()) {
      const fb = Number(form.ai_routing_fallback_provider_id);
      if (!Number.isInteger(fb) || fb <= 0)
        return { ok: false, errMsg: "兜底模型提供商必须有效" };
      cfg.routing_fallback_provider_id = fb;
    }
    if (form.ai_classifier_provider_id.trim()) {
      const cls = Number(form.ai_classifier_provider_id);
      if (!Number.isInteger(cls) || cls <= 0)
        return { ok: false, errMsg: "分类器模型提供商必须有效" };
      cfg.classifier_provider_id = cls;
    }
  }
  // 输出格式（默认 html + 空模板 = 用后端的 PRESET_SIMPLE）
  cfg.output_format = form.ai_output_format;
  if (form.ai_output_template.trim()) {
    cfg.output_template = form.ai_output_template;
  }
  // escape_values 默认 true；非默认值才下发
  if (!form.ai_escape_values) {
    cfg.escape_values = false;
  }
  if (form.ai_web_search) {
    cfg.web_search = true;
    cfg.web_search_context_size = form.ai_web_search_context_size;
  }
  // 发送方式：edit 是默认，仅 send_new 才下发
  if (form.ai_send_mode === "send_new") {
    cfg.send_mode = "send_new";
  }
  return { ok: true, aliases, config: cfg };
}

export function CommandTemplates() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const listQ = useQuery({
    queryKey: ["cmd-tpl"],
    queryFn: listCommandTemplates,
  });
  const providersQ = useQuery({
    queryKey: ["llm-providers"],
    queryFn: listLLMProviders,
  });
  // 实时拉系统命令前缀，用在编辑器的"`,name` 触发"那行提示——避免硬编码逗号
  // 跟系统设置改了不一致
  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const cmdPrefix = settingsQ.data?.command_prefix || ",";
  const providerIds = useMemo(
    () => new Set((providersQ.data || []).map((p) => p.id)),
    [providersQ.data],
  );
  const hasProviders = (providersQ.data?.length || 0) > 0;
  const providerUnavailable = providersQ.isSuccess && !hasProviders;

  const [editing, setEditing] = useState<FormState | null>(null);

  const createMut = useMutation({
    mutationFn: (form: FormState) => {
      const r = buildPayload(form);
      if (!r.ok) throw new Error(r.errMsg || "config 校验失败");
      return createCommandTemplate({
        name: form.name.trim(),
        aliases: r.aliases || [],
        type: form.type,
        config: r.config!,
        description: form.description || null,
      });
    },
    onSuccess: () => {
      toast.success("已新建模板");
      qc.invalidateQueries({ queryKey: ["cmd-tpl"] });
      setEditing(null);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const updateMut = useMutation({
    mutationFn: (form: FormState) => {
      if (!form.id) throw new Error("缺少 id");
      const r = buildPayload(form);
      if (!r.ok) throw new Error(r.errMsg || "config 校验失败");
      return patchCommandTemplate(form.id, {
        name: form.name.trim(),
        aliases: r.aliases || [],
        type: form.type,
        config: r.config!,
        description: form.description || null,
      });
    },
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["cmd-tpl"] });
      setEditing(null);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteCommandTemplate(id),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["cmd-tpl"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <div className="space-y-6">
      <BuiltinCommandsPanel cmdPrefix={cmdPrefix} />
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle className="text-base">自定义命令模板</CardTitle>
              <CardDescription>
                全局模板库，每条 = 一个 <code>{cmdPrefix}name</code> 命令的"配方"。账号详情 → 自定义命令页签选择是否启用
              </CardDescription>
            </div>
            <Button size="sm" onClick={() => setEditing({ ...EMPTY_FORM })}>
              <Plus className="mr-1 h-4 w-4" /> 新建
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {providerUnavailable ? (
            <div className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              AI 命令模板不可用：先去模型提供商添加配置。
              <Button
                type="button"
                variant="link"
                className="h-auto px-1 py-0 text-xs"
                onClick={() => nav("/ai/providers")}
              >
                前往模型提供商
              </Button>
            </div>
          ) : null}
          {listQ.isLoading ? (
            <div className="flex h-20 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : listQ.data && listQ.data.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>命令指令</TableHead>
                  <TableHead>命令类型</TableHead>
                  <TableHead>别名（短命令）</TableHead>
                  <TableHead>说明</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {listQ.data.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="font-mono text-sm">{cmdPrefix}{t.name}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap items-center gap-1">
                        <Badge variant="secondary">{TYPE_LABELS[t.type] || t.type}</Badge>
                        {t.type === "ai" &&
                        providersQ.isSuccess &&
                        typeof t.config?.provider_id === "number" &&
                        !providerIds.has(t.config.provider_id) ? (
                          <Badge variant="destructive">模型提供商缺失</Badge>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[260px]">
                      <div className="flex flex-wrap gap-1">
                        {(t.aliases || []).length === 0 ? (
                          <span className="text-xs text-muted-foreground">—</span>
                        ) : (
                          (t.aliases || []).map((a) => (
                            <Badge key={a} variant="outline" className="font-mono text-[11px]">
                              {cmdPrefix}{a}
                            </Badge>
                          ))
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[420px] truncate text-xs text-muted-foreground">
                      {t.description || "—"}
                    </TableCell>
                    <TableCell className="space-x-2 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditing(formFromTemplate(t))}
                      >
                        <Edit3 className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={deleteMut.isPending}
                        onClick={() => {
                          if (
                            confirm(
                              `确认删除模板「${t.name}」？所有启用此模板的账号都会失去这个命令`,
                            )
                          ) {
                            deleteMut.mutate(t.id);
                          }
                        }}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="rounded-md border border-dashed py-8 text-center text-xs text-muted-foreground">
              尚无模板。新建一个后即可在账号详情中勾选启用
            </p>
          )}
        </CardContent>

        {editing && (
          <CommandEditDialog
            form={editing}
            cmdPrefix={cmdPrefix}
            onChange={setEditing}
            onCancel={() => setEditing(null)}
            onSave={() => {
              const trimName = editing.name.trim();
              if (!NAME_RE.test(trimName)) {
                toast.error("命令名只能包含字母 / 数字 / 下划线，1-64 字符");
                return;
              }
              if (editing.type === "ai" && providerUnavailable) {
                toast.error("先去模型提供商添加配置");
                return;
              }
              if (editing.id) {
                updateMut.mutate(editing);
              } else {
                createMut.mutate(editing);
              }
            }}
            saving={createMut.isPending || updateMut.isPending}
            hasProviders={hasProviders}
            providerUnavailable={providerUnavailable}
            onGoProviders={() => nav("/ai/providers")}
          />
        )}
      </Card>
    </div>
  );
}

// ── 内置命令面板（只读） ──────────────────────────────────────────
// 让用户起自定义模板名时知道哪些已被占用；防撞名（API 校验也会拒）。
function BuiltinCommandsPanel({ cmdPrefix }: { cmdPrefix: string }) {
  const builtinQ = useQuery({
    queryKey: ["cmd-builtin"],
    queryFn: listBuiltinCommands,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">内置命令（只读）</CardTitle>
        <CardDescription>
          系统注册在 worker 里的命令；自定义模板的 name/aliases 不能与此重复
        </CardDescription>
      </CardHeader>
      <CardContent>
        {builtinQ.isLoading ? (
          <div className="flex h-16 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : builtinQ.data && builtinQ.data.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {builtinQ.data.map((c) => (
              <span
                key={c.name}
                title={c.doc || c.name}
                className="inline-flex items-center gap-1.5 rounded-md border bg-muted/50 px-2 py-1 text-xs"
              >
                <code className="font-mono font-medium">
                  {cmdPrefix}
                  {c.name}
                </code>
                {c.aliases.length > 0 ? (
                  <span className="text-muted-foreground">
                    ({c.aliases.map((a) => `${cmdPrefix}${a}`).join(", ")})
                  </span>
                ) : null}
                {c.doc ? (
                  <span className="text-muted-foreground">— {c.doc}</span>
                ) : null}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">未读取到内置命令</p>
        )}
      </CardContent>
    </Card>
  );
}

function CommandEditDialog({
  form,
  cmdPrefix,
  onChange,
  onCancel,
  onSave,
  saving,
  hasProviders,
  providerUnavailable,
  onGoProviders,
}: {
  form: FormState;
  cmdPrefix: string;
  onChange: (s: FormState) => void;
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
  hasProviders: boolean;
  providerUnavailable: boolean;
  onGoProviders: () => void;
}) {
  const isEdit = !!form.id;
  const setField = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    onChange({ ...form, [k]: v });
  // 一次性设置多个字段；用于"必须同步更新"的字段对（比如 provider_id + model）。
  // 不要拆成两次 setField——`form` 是闭包里的旧值，第二次 setField 会用旧 form
  // 把第一次的改动盖掉，结果两个字段都没改成。
  const setFields = (patch: Partial<FormState>) =>
    onChange({ ...form, ...patch });

  // ai 类型才需要拉 provider 列表
  const providersQ = useQuery({
    queryKey: ["llm-providers"],
    queryFn: listLLMProviders,
    enabled: form.type === "ai",
  });

  // 切类型时清相邻字段，避免上次填的脏数据落到 config
  const typeOptions = useMemo(
    () => Object.entries(TYPE_LABELS) as [CommandTemplateType, string][],
    [],
  );

  return (
    <Dialog open onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑" : "新建"} 自定义命令</DialogTitle>
          <DialogDescription>
            根据类型不同，下方表单会切到对应字段，*为必填项
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>命令指令 *</Label>
              <Input
                value={form.name}
                maxLength={64}
                placeholder="hi / ai / forward_to_devs"
                onChange={(e) => setField("name", e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                只允许字母 / 数字 / 下划线；当前为发送【 <code>{cmdPrefix}{form.name || "name"}</code> 】触发
              </p>
            </div>
            <div className="space-y-1.5">
              <Label>类型 *</Label>
              <Select
                value={form.type}
                onChange={(e) =>
                  setField("type", e.target.value as CommandTemplateType)
                }
              >
                {typeOptions.map(([k, label]) => (
                  <option key={k} value={k} disabled={k === "ai" && providerUnavailable}>
                    {label}（{k}）
                  </option>
                ))}
              </Select>
              {providerUnavailable ? (
                <p className="text-xs text-amber-700">
                  AI 类型暂不可选，先去 AI 模块添加模型提供商。
                  <Button
                    type="button"
                    variant="link"
                    className="h-auto px-1 py-0 text-xs"
                    onClick={onGoProviders}
                  >
                    前往模型提供商
                  </Button>
                </p>
              ) : null}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>说明（可选）</Label>
            <Input
              value={form.description}
              maxLength={255}
              placeholder={`便于 ${cmdPrefix}help 显示`}
              onChange={(e) => setField("description", e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>别名 / 短命令（可选）</Label>
            <Input
              value={form.aliases_text}
              maxLength={255}
              placeholder="t, trans, tr"
              onChange={(e) => setField("aliases_text", e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              用逗号或空格分隔，规则同命令指令；例如 <code>t, trans</code>
            </p>
          </div>

          {/* 按 type 切不同子表单 */}
          {form.type === "reply_text" && (
            <div className="space-y-1.5">
              <Label>回复文本 *</Label>
              <Textarea
                value={form.text}
                rows={4}
                placeholder="hello {args}"
                onChange={(e) => setField("text", e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                支持 `{"{args}"}` 占位符，会被命令后跟的参数替换
              </p>
            </div>
          )}

          {form.type === "forward_to" && (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label>目标 chat_id（可选）</Label>
                <Input
                  inputMode="numeric"
                  value={form.target_chat_id}
                  onChange={(e) =>
                    setField(
                      "target_chat_id",
                      e.target.value.replace(/[^\d-]/g, ""),
                    )
                  }
                  placeholder="留空 = 转到当前会话；填如 -1001234567890"
                />
                <p className="text-xs text-muted-foreground">
                  留空 = 触发命令时<strong>默认转发到命令消息所在的 chat</strong>。
                  填了就强制转到这个 chat_id；在该群里执行 <code>{cmdPrefix}id</code> 可获得 chat_id（超级群以 -100 开头）。
                </p>
              </div>
              <div className="space-y-1.5">
                <Label>转发方式</Label>
                <Select
                  value={form.forward_mode}
                  onChange={(e) => setField("forward_mode", e.target.value)}
                >
                  <option value="forward_native">原生转发（携带原作者）</option>
                  <option value="copy_text">复制文本（不显示原作者）</option>
                  <option value="quote">引用包装（带"来自 X"前缀）</option>
                  <option value="link_only">仅发链接（公开群可点）</option>
                </Select>
              </div>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label>成功后立即删除命令消息</Label>
                  <Switch
                    checked={form.forward_delete_immediately}
                    onCheckedChange={(v) =>
                      setField("forward_delete_immediately", v)
                    }
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  开启后，命令触发成功后立即删除你发的 <code>{cmdPrefix}{form.name || "name"}</code> 命令消息（不影响转发/回复的内容）。
                </p>
              </div>
              {!form.forward_delete_immediately && (
                <div className="space-y-1.5">
                  <Label>触发后自动删除命令消息（秒，可选）</Label>
                  <Input
                    inputMode="numeric"
                    value={form.forward_delete_after}
                    maxLength={5}
                    onChange={(e) =>
                      setField(
                        "forward_delete_after",
                        e.target.value.replace(/[^\d]/g, ""),
                      )
                    }
                    placeholder="留空或 0 = 不删；如 5 = 5 秒后删命令消息"
                  />
                  <p className="text-xs text-muted-foreground">
                    转发成功后等待 N 秒，删除你刚发的 <code>{cmdPrefix}{form.name || "name"}</code> 命令消息（不影响转过去的内容）。范围 0–3600；不删保留 ✓ 提示。
                  </p>
                </div>
              )}
            </div>
          )}

          {form.type === "run_plugin" && (
            <div className="space-y-3">
              <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                调用已加载插件注册的命令；method 留空时默认使用 plugin_key 同名命令。
              </div>
              <div className="space-y-1.5">
                <Label>plugin_key *</Label>
                <Input
                  value={form.plugin_key}
                  maxLength={64}
                  onChange={(e) => setField("plugin_key", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>method（可选）</Label>
                <Input
                  value={form.plugin_method}
                  maxLength={64}
                  onChange={(e) => setField("plugin_method", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>args（JSON 数组）</Label>
                <Input
                  value={form.plugin_args}
                  onChange={(e) => setField("plugin_args", e.target.value)}
                  placeholder='[]'
                />
              </div>
            </div>
          )}

          {form.type === "ai" && (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label>
                  {form.ai_routing_mode === "auto"
                    ? "默认 / 兜底模型 *"
                    : "提供商 + 模型 *"}
                </Label>
                <ProviderModelSelect
                  value={
                    form.ai_provider_id && form.ai_model
                      ? `${form.ai_provider_id}|${form.ai_model}`
                      : form.ai_provider_id
                        ? `${form.ai_provider_id}|`
                        : ""
                  }
                  providers={providersQ.data}
                  loading={providersQ.isLoading}
                  onChange={(v) => {
                    // 选项 value 形如 "<pid>|<model>"
                    // 必须用 setFields 一次性写两个字段——拆成两次 setField 会
                    // 因为闭包里的旧 form 互相覆盖，最终两个字段都回到空值
                    const sep = v.indexOf("|");
                    if (sep < 0) {
                      setFields({ ai_provider_id: "", ai_model: "" });
                      return;
                    }
                    const pid = v.slice(0, sep);
                    const model = v.slice(sep + 1);
                    setFields({ ai_provider_id: pid, ai_model: model });
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  下拉里每条 = 一个已启用的 (提供商 × 模型) 组合。要新增/启用模型去
                  <span className="mx-1 font-medium">AI 模块 → 模型提供商</span>编辑。
                  {form.ai_routing_mode === "auto"
                    ? " auto 模式下，规则未命中且未设独立兜底时走这条"
                    : ""}
                </p>
              </div>
              <div className="space-y-1.5">
                <Label>System Prompt 系统默认提示词</Label>
                <Textarea
                  value={form.ai_system_prompt}
                  rows={3}
                  onChange={(e) => setField("ai_system_prompt", e.target.value)}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label>max_tokens</Label>
                  <Input
                    inputMode="numeric"
                    value={form.ai_max_tokens}
                    onChange={(e) =>
                      setField(
                        "ai_max_tokens",
                        e.target.value.replace(/[^\d]/g, ""),
                      )
                    }
                  />
                </div>
                <div className="flex items-center gap-2 self-end pb-2">
                  <Switch
                    checked={form.ai_quote_replied}
                    onCheckedChange={(v) => setField("ai_quote_replied", v)}
                    id="quoteReplied"
                  />
                  <Label htmlFor="quoteReplied" className="cursor-pointer">
                    引用被回复消息的内容作为提示词的一部分
                  </Label>
                </div>
              </div>

              {/* ── 发送方式 ─────────────────────────────── */}
              <div className="space-y-1.5">
                <Label>发送方式</Label>
                <Select
                  value={form.ai_send_mode}
                  onChange={(e) =>
                    setField(
                      "ai_send_mode",
                      e.target.value === "send_new" ? "send_new" : "edit",
                    )
                  }
                >
                  <option value="edit">原地编辑命令消息（默认）</option>
                  <option value="send_new">删命令、发新消息（不带 reply_to）</option>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {form.ai_send_mode === "send_new" ? (
                    <>
                      用于"我回复某人后用 <code>{cmdPrefix}{form.name || "ai"}</code> 提问"的场景：
                      被回复消息**只作为模型上下文**，发出去的回答**不再是 reply**，
                      也不会在被回复方那里留下"你回复了我"的痕迹（首次发命令的 ping 仍无法避免）。
                      此模式下 <code>{"{display_input}"}</code> 自动回退为你打的字（不复读对方原文）。
                    </>
                  ) : (
                    <>
                      原地把命令消息编辑成 AI 回答，保留 reply 链 — 在群里能让上下文清晰可追溯。
                    </>
                  )}
                </p>
              </div>

              {/* ── 路由模式 ────────────────────────────── */}
              <div className="rounded-md border bg-muted/30 p-3 space-y-3">
                <div>
                  <Label className="text-sm font-semibold">路由模式</Label>
                  <p className="text-xs text-muted-foreground">
                    fixed = 固定使用上面选的模型；auto = 看消息类型自动路由调用模型（详见 AI
                    设置页推荐配置）
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <label
                    className={
                      "cursor-pointer rounded-md border p-3 text-sm transition-colors " +
                      (form.ai_routing_mode === "fixed"
                        ? "border-primary bg-primary/5"
                        : "hover:bg-muted")
                    }
                  >
                    <input
                      type="radio"
                      name="routingMode"
                      className="mr-2"
                      checked={form.ai_routing_mode === "fixed"}
                      onChange={() => setField("ai_routing_mode", "fixed")}
                    />
                    <span className="font-medium">fixed（固定）</span>
                    <p className="mt-1 text-xs text-muted-foreground">
                      简单可控；适合"我就要某个模型"
                    </p>
                  </label>
                  <label
                    className={
                      "cursor-pointer rounded-md border p-3 text-sm transition-colors " +
                      (form.ai_routing_mode === "auto"
                        ? "border-primary bg-primary/5"
                        : "hover:bg-muted")
                    }
                  >
                    <input
                      type="radio"
                      name="routingMode"
                      className="mr-2"
                      checked={form.ai_routing_mode === "auto"}
                      onChange={() => setField("ai_routing_mode", "auto")}
                    />
                    <span className="font-medium">auto（自动路由）</span>
                    <p className="mt-1 text-xs text-muted-foreground">
                      按消息类型选合适的 模型；省钱 + 更对路
                    </p>
                  </label>
                </div>

                {form.ai_routing_mode === "auto" && (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <Label>独立兜底模型提供商（可选）</Label>
                      <ProviderSelect
                        value={form.ai_routing_fallback_provider_id}
                        providers={providersQ.data}
                        loading={providersQ.isLoading}
                        onChange={(v) =>
                          setField("ai_routing_fallback_provider_id", v)
                        }
                        allowEmpty
                      />
                      <p className="text-xs text-muted-foreground">
                        留空 = 直接复用上面那条「默认 / 兜底模型提供商」；想分开就在这选另一条
                      </p>
                    </div>
                    <div className="space-y-1.5">
                      <Label>分类器模型提供商（可选）</Label>
                      <ProviderSelect
                        value={form.ai_classifier_provider_id}
                        providers={providersQ.data}
                        loading={providersQ.isLoading}
                        onChange={(v) =>
                          setField("ai_classifier_provider_id", v)
                        }
                        allowEmpty
                      />
                      <p className="text-xs text-muted-foreground">
                        指定后：规则未命中时调一个轻量小模型（建议 tag=classify、cost_tier=1）让它
                        判断 code/math/translate/vision/reason/chat 中的哪一个
                      </p>
                    </div>
                  </div>
                )}
              </div>

              {/* ── 联网搜索 ────────────────────────────── */}
              <div className="rounded-md border bg-muted/30 p-3 space-y-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <Label className="inline-flex items-center gap-1.5 text-sm font-semibold">
                      <Search className="h-4 w-4" /> 联网搜索
                    </Label>
                    <p className="mt-1 text-xs text-muted-foreground">
                      开启后，本命令会调用模型的搜索工具并可在模板里用 <code>{"{sources}"}</code> 显示来源。
                    </p>
                  </div>
                  <Switch
                    checked={form.ai_web_search}
                    onCheckedChange={(v) => setField("ai_web_search", v)}
                    id="aiWebSearch"
                  />
                </div>
                {form.ai_web_search && (
                  <div className="grid gap-3 sm:grid-cols-[220px_1fr]">
                    <div className="space-y-1.5">
                      <Label>搜索上下文强度</Label>
                      <Select
                        value={form.ai_web_search_context_size}
                        onChange={(e) =>
                          setField(
                            "ai_web_search_context_size",
                            e.target.value as "low" | "medium" | "high",
                          )
                        }
                      >
                        <option value="low">低：更快，更省</option>
                        <option value="medium">中：默认平衡</option>
                        <option value="high">高：更多上下文</option>
                      </Select>
                    </div>
                    <p className="self-end pb-2 text-xs text-muted-foreground">
                      当前后端仅对 OpenAI Responses API 传 <code>web_search</code> 工具。
                      选择 Chat Completions 或 Anthropic provider 时会给出明确错误提示。
                    </p>
                  </div>
                )}
              </div>

              {/* ── 消息格式 ─────────────────────────────── */}
              <OutputTemplateEditor
                outputFormat={form.ai_output_format}
                onOutputFormatChange={(v) => setField("ai_output_format", v)}
                template={form.ai_output_template}
                onTemplateChange={(v) => setField("ai_output_template", v)}
                escapeValues={form.ai_escape_values}
                onEscapeValuesChange={(v) => setField("ai_escape_values", v)}
              />

              <p className="text-xs text-muted-foreground">
                调用流程：用户在 TG 中回复某消息并发 <code>{cmdPrefix}{form.name || "ai"} 问题</code>，worker 将「被回复消息正文 + 问题」拼成 用户提示词，把回答编辑回原消息
              </p>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onCancel} disabled={saving}>
            取消
          </Button>
          <Button onClick={onSave} disabled={saving}>
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ProviderSelect({
  value,
  providers,
  loading,
  onChange,
  allowEmpty = false,
}: {
  value: string;
  providers?: LLMProviderOut[];
  loading: boolean;
  onChange: (v: string) => void;
  /** 允许"不选"；选了就 value="" 上送（CommandTemplates 在保存时会按情况省略字段） */
  allowEmpty?: boolean;
}) {
  if (loading) {
    return (
      <div className="flex h-10 items-center gap-2 rounded-md border px-3 text-xs text-muted-foreground">
        <Spinner className="text-primary" /> 加载中…
      </div>
    );
  }
  if (!providers || providers.length === 0) {
    return (
      <div className="rounded-md border px-3 py-2 text-xs alert-warning">
        尚未配置模型提供商。先到「AI 模块 → 模型提供商」新建一个
      </div>
    );
  }
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{allowEmpty ? "— 不指定 —" : "— 请选择 —"}</option>
      {providers.map((p) => (
        <option key={p.id} value={String(p.id)}>
          {p.name}（{p.provider} · {p.default_model}）
          {p.has_api_key ? "" : " · ⚠ 未配置 key"}
          {p.tags && p.tags.length > 0 ? ` · [${p.tags.join(",")}]` : ""}
        </option>
      ))}
    </Select>
  );
}

/**
 * 展开式 provider × model 选择器：
 *
 * 每个启用的 (provider, model) 组合 = 一个候选选项，形如
 * ``Any（OpenAI · gpt-5.5）``。选项 value 编码为 ``{provider_id}|{model}``，
 * 上层在 ``buildPayload`` 里拆开写到 ``cfg.provider_id`` + ``cfg.model``。
 *
 * 如果某 provider 还没启用任何模型，会自动展开成"用 default_model"那条
 * 选项（向后兼容老配置：以前 provider.default_model 直接作为模型）。
 *
 * value 是 ``"<pid>|<model>"`` 的形式；onChange 回传同样格式。
 */
function ProviderModelSelect({
  value,
  providers,
  loading,
  onChange,
}: {
  value: string;
  providers?: LLMProviderOut[];
  loading: boolean;
  onChange: (v: string) => void;
}) {
  if (loading) {
    return (
      <div className="flex h-10 items-center gap-2 rounded-md border px-3 text-xs text-muted-foreground">
        <Spinner className="text-primary" /> 加载中…
      </div>
    );
  }
  if (!providers || providers.length === 0) {
    return (
      <div className="rounded-md border px-3 py-2 text-xs alert-warning">
        尚未配置模型提供商。先到「AI 模块 → 模型提供商」新建一个，并在编辑里拉取并启用至少一个模型
      </div>
    );
  }

  // 把每个 provider 展开成"启用的模型"列表
  // 每个 provider 都额外加一条 "用提供商默认（→ default_model）" 行；选了它后保存时
  // cfg.model 不下发，worker 调用时 build_client 会按 provider.default_model 走——
  // 这样用户改 default_model 后所有这种"默认"模板自动跟着变，不用一个个改模板
  type Row = {
    pid: number;
    providerName: string;
    providerKind: string;
    /** 空字符串 = "用提供商默认"；非空 = 具体 model id */
    modelId: string;
    /** 仅"用提供商默认"行才有值；UI 展示成 → gpt-5.5 让用户知道当前默认是啥 */
    defaultModelHint: string | null;
    custom: boolean;
    hasKey: boolean;
  };
  const rows: Row[] = [];
  for (const p of providers) {
    if (!p.has_api_key && (p.provider !== "ollama")) {
      // 没配 key 的 provider 不展开（除了 ollama 本地不需要 key）
      // 但仍想让用户看到，所以加一条 disabled 提示行——这里简单跳过
      // 没有 key 的还是要展示，让用户知道这条 provider 没法用，他能去配
    }
    // (1) 顶部一行：用提供商默认
    rows.push({
      pid: p.id,
      providerName: p.name,
      providerKind: String(p.provider),
      modelId: "",
      defaultModelHint: p.default_model || null,
      custom: false,
      hasKey: !!p.has_api_key,
    });
    // (2) 已启用的具体模型一一展开
    const enabled = (p.models || []).filter((m) => m.enabled);
    for (const m of enabled) {
      rows.push({
        pid: p.id,
        providerName: p.name,
        providerKind: String(p.provider),
        modelId: m.id,
        defaultModelHint: null,
        custom: !!m.custom,
        hasKey: !!p.has_api_key,
      });
    }
  }

  if (rows.length === 0) {
    return (
      <div className="rounded-md border px-3 py-2 text-xs alert-warning">
        所有提供商都没启用任何模型。在「模型提供商」编辑某个提供商，启用至少一条模型再来
      </div>
    );
  }

  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">— 请选择 —</option>
      {rows.map((r) => {
        // value 形如 "<pid>|<model>"；"用默认"那条 model 部分空
        const v = `${r.pid}|${r.modelId}`;
        const label =
          r.modelId === ""
            ? `${r.providerName}（${r.providerKind} · 用提供商默认${r.defaultModelHint ? ` → ${r.defaultModelHint}` : ""}）` +
            (r.hasKey ? "" : " · ⚠ 未配置 key")
            : `${r.providerName}（${r.providerKind} · ${r.modelId}）` +
            (r.hasKey ? "" : " · ⚠ 未配置 key");
        return (
          <option key={v} value={v}>
            {label}
          </option>
        );
      })}
    </Select>
  );
}
