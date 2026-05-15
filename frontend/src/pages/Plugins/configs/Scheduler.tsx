import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Pencil, Play, Plus, Trash2, Zap } from "lucide-react";
import { toast } from "sonner";

import { getSystemSettings } from "@/api/system";
import { listAccounts } from "@/api/accounts";
import { executeRule } from "@/api/features";
import type {
  RuleDryRunResponse,
  RuleExecuteResponse,
  RuleOut,
  SchedulerRuleConfig,
} from "@/api/types";
import { Button } from "@/components/ui/button";
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
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { getErrMsg } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";
import { DryRunDetail } from "@/components/DryRunDetail";
import {
  Field,
  RuleEditDialogShell,
  RulePageHeader,
  useRuleCrud,
} from "./_shared";

function defaultConfig(): SchedulerRuleConfig {
  return {
    kind: "cron",
    cron: "*/5 * * * *",
    fire_at: "",
    interval_sec: 300,
    enabled: true,
    action: {
      type: "send_message",
      target_chat_id: 0,
      text: "tick",
      command: ",help",
      provider_id: 0,
      prompt: "今天要做什么？",
      system_prompt: "你是简洁有用的中文助手。",
      max_tokens: 256,
      delete_after: null,
    },
    next_fire: null,
  };
}

function readConfig(c: Record<string, unknown> | undefined): SchedulerRuleConfig {
  return { ...defaultConfig(), ...(c as Partial<SchedulerRuleConfig> | undefined) };
}

interface FormState {
  name: string;
  enabled: boolean;
  priority: number;
  config: SchedulerRuleConfig;
}

function emptyForm(): FormState {
  return { name: "", enabled: true, priority: 100, config: defaultConfig() };
}

export function SchedulerConfig() {
  const params = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const aidFromPath = Number(params.aid);
  const aidFromQuery = Number(searchParams.get("aid"));
  const aid =
    Number.isFinite(aidFromPath) && aidFromPath > 0
      ? aidFromPath
      : Number.isFinite(aidFromQuery) && aidFromQuery > 0
        ? aidFromQuery
        : 0;
  const fromAccountRoute = Number.isFinite(aidFromPath) && aidFromPath > 0;
  const nav = useNavigate();

  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  const tzQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const tz = tzQ.data?.timezone || "";

  // Scheduler 不需要 featureKey（没有"功能总开关"语义）
  const crud = useRuleCrud({ aid, ruleKind: "scheduler" });

  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<RuleOut | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm());

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setEditOpen(true);
  }

  function openEdit(r: RuleOut) {
    setEditing(r);
    setForm({
      name: r.name,
      enabled: r.enabled,
      priority: r.priority,
      config: readConfig(r.config),
    });
    setEditOpen(true);
  }

  async function handleSave() {
    if (!form.name.trim()) {
      toast.error("规则名称必填");
      return;
    }
    const cfg = form.config;
    if (cfg.kind === "cron" && !(cfg.cron || "").trim()) {
      toast.error("cron 表达式必填");
      return;
    }
    if (cfg.kind === "once" && !(cfg.fire_at || "").trim()) {
      toast.error("once 模式 fire_at 必填");
      return;
    }
    if (cfg.kind === "interval" && Number(cfg.interval_sec || 0) <= 0) {
      toast.error("interval_sec 必须 > 0");
      return;
    }
    if (!cfg.action?.type) {
      toast.error("action.type 必填");
      return;
    }
    if (
      ["send_message", "call_llm"].includes(cfg.action.type) &&
      !cfg.action.target_chat_id
    ) {
      toast.error("target_chat_id 必填");
      return;
    }
    if (cfg.action.type === "send_message" && !(cfg.action.text || "").trim()) {
      toast.error("send_message 的 text 必填");
      return;
    }
    if (
      cfg.action.type === "run_command" &&
      !(cfg.action.command || cfg.action.text || "").trim()
    ) {
      toast.error("run_command 的 command 必填");
      return;
    }
    if (cfg.action.type === "call_llm") {
      if (!cfg.action.provider_id) {
        toast.error("call_llm 的 provider_id 必填");
        return;
      }
      if (!(cfg.action.prompt || "").trim()) {
        toast.error("call_llm 的 prompt 必填");
        return;
      }
    }
    if (cfg.action.delete_after != null && cfg.action.delete_after > 3600) {
      toast.error("delete_after 上限为 3600 秒");
      return;
    }

    await crud.saveRule({
      editing,
      payload: {
        name: form.name.trim(),
        enabled: form.enabled,
        priority: form.priority,
        config: form.config as unknown as Record<string, unknown>,
      },
      onSuccess: () => setEditOpen(false),
    });
  }

  const [dryOpen, setDryOpen] = useState(false);
  const [dryRule, setDryRule] = useState<RuleOut | null>(null);
  const [dryResult, setDryResult] = useState<RuleDryRunResponse | null>(null);

  function openDryRun(rule: RuleOut) {
    setDryRule(rule);
    setDryResult(null);
    setDryOpen(true);
  }

  function handleDryRun() {
    if (!dryRule) return;
    crud.dryRun({
      rid: dryRule.id,
      payload: {
        sample_message: "scheduler dry-run",
        sample_chat_type: "private",
      },
      onSuccess: (res) => setDryResult(res),
    });
  }

  // executeRule 是 Scheduler 独有，不进 useRuleCrud
  const [execOpen, setExecOpen] = useState(false);
  const [execRule, setExecRule] = useState<RuleOut | null>(null);
  const [execResult, setExecResult] = useState<RuleExecuteResponse | null>(null);

  function openExec(rule: RuleOut) {
    setExecRule(rule);
    setExecResult(null);
    setExecOpen(true);
  }

  const execMut = useMutation({
    mutationFn: () => executeRule(aid, "scheduler", execRule!.id),
    onSuccess: (res) => {
      setExecResult(res);
      if (res.ok) {
        crud.rulesQ.refetch();
      }
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (!aid) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">定时任务</h1>
          <p className="text-sm text-muted-foreground">
            选择账号后管理该账号的定时任务规则。
          </p>
        </div>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">选择账号</CardTitle>
            <CardDescription>
              定时任务按账号隔离运行，每个账号独立维护规则。
            </CardDescription>
          </CardHeader>
          <CardContent>
            {accountsQ.isLoading ? (
              <div className="flex h-20 items-center justify-center">
                <Spinner className="text-primary" />
              </div>
            ) : accountsQ.data && accountsQ.data.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {accountsQ.data.map((a) => (
                  <Button
                    key={a.id}
                    variant="outline"
                    onClick={() => setSearchParams({ aid: String(a.id) })}
                  >
                    {a.display_name || a.phone || `账号 #${a.id}`}
                  </Button>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                暂无可用账号，请先绑定账号。
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <RulePageHeader
        title={`定时任务 · 账号 #${aid}`}
        backLabel={fromAccountRoute ? "返回账号" : "返回定时任务"}
        backHref={fromAccountRoute ? `/accounts/${aid}?tab=features` : "/plugins/scheduler"}
      />

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">基础能力状态</CardTitle>
              <CardDescription>
                定时任务调度器随 worker 初始化运行；是否执行由每条规则自己的启用状态控制。
              </CardDescription>
            </div>
            <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
              随 worker 启动
            </span>
          </div>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">规则</CardTitle>
              <CardDescription>
                支持 cron 定时 / once 单次 / interval 间隔，触发动作：发送消息 / 执行命令 / 调用 LLM
              </CardDescription>
            </div>
            <Button onClick={openCreate}>
              <Plus className="mr-1 h-4 w-4" /> 新建规则
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {crud.rulesQ.isLoading ? (
            <div className="flex h-20 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : crud.rulesQ.data && crud.rulesQ.data.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>启用</TableHead>
                  <TableHead>优先级</TableHead>
                  <TableHead>触发</TableHead>
                  <TableHead>动作</TableHead>
                  <TableHead>下次触发</TableHead>
                  <TableHead>上次触发</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {crud.rulesQ.data.map((r) => {
                  const cfg = readConfig(r.config);
                  return (
                    <TableRow key={r.id}>
                      <TableCell className="font-medium">{r.name}</TableCell>
                      <TableCell>
                        <Badge variant={r.enabled ? "success" : "secondary"}>
                          {r.enabled ? "ON" : "OFF"}
                        </Badge>
                      </TableCell>
                      <TableCell>{r.priority}</TableCell>
                      <TableCell>{triggerLabel(cfg)}</TableCell>
                      <TableCell>
                        <div className="flex flex-col gap-0.5">
                          <span>
                            {ACTION_TYPE_LABELS[cfg.action?.type || "send_message"] ||
                              cfg.action?.type}
                          </span>
                          {cfg.action?.delete_after ? (
                            <span className="text-xs text-muted-foreground">
                              自动删除: {cfg.action.delete_after}s
                            </span>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell className="text-xs font-mono">
                        {formatDateTime(cfg.next_fire, tz)}
                      </TableCell>
                      <TableCell className="text-xs font-mono">
                        {formatDateTime(cfg.last_fire, tz)}
                      </TableCell>
                      <TableCell>
                        {cfg.last_result ? (
                          <div className="flex flex-col gap-0.5">
                            <Badge
                              variant={cfg.last_result === "ok" ? "success" : "destructive"}
                            >
                              {cfg.last_result === "ok" ? "成功" : "失败"}
                            </Badge>
                            {cfg.last_error ? (
                              <span
                                className="max-w-[160px] truncate text-xs text-destructive"
                                title={cfg.last_error}
                              >
                                {cfg.last_error}
                              </span>
                            ) : null}
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">未执行</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="inline-flex gap-1">
                          <Button size="sm" variant="ghost" onClick={() => openEdit(r)}>
                            <Pencil className="mr-1 h-3.5 w-3.5" /> 编辑
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => openDryRun(r)}>
                            <Play className="mr-1 h-3.5 w-3.5" /> 试运行
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => openExec(r)}>
                            <Zap className="mr-1 h-3.5 w-3.5" /> 执行
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-destructive"
                            onClick={() => {
                              if (confirm(`删除规则 ${r.name}？`)) crud.removeRule(r.id);
                            }}
                          >
                            <Trash2 className="mr-1 h-3.5 w-3.5" /> 删除
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">暂无规则，点击"新建规则"。</p>
          )}
        </CardContent>
      </Card>

      <RuleEditDialogShell
        open={editOpen}
        onOpenChange={setEditOpen}
        editing={editing}
        description="保存后由 worker 热更新，无需重启。"
        maxWidthClass="max-w-2xl"
        name={form.name}
        enabled={form.enabled}
        priority={form.priority}
        onNameChange={(v) => setForm((s) => ({ ...s, name: v }))}
        onEnabledChange={(v) => setForm((s) => ({ ...s, enabled: v }))}
        onPriorityChange={(v) => setForm((s) => ({ ...s, priority: v }))}
        onSave={handleSave}
        saving={crud.saving}
      >
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Field label="触发类型">
            <Select
              value={form.config.kind}
              onChange={(e) =>
                setForm((s) => ({
                  ...s,
                  config: {
                    ...s.config,
                    kind: e.target.value as SchedulerRuleConfig["kind"],
                  },
                }))
              }
            >
              <option value="cron">cron 定时</option>
              <option value="once">once 单次</option>
              <option value="interval">interval 间隔</option>
            </Select>
          </Field>
          {form.config.kind === "cron" ? (
            <Field label="cron 表达式">
              <Input
                value={form.config.cron || ""}
                onChange={(e) =>
                  setForm((s) => ({
                    ...s,
                    config: { ...s.config, cron: e.target.value },
                  }))
                }
                placeholder="*/5 * * * *"
              />
              <p className="text-xs text-muted-foreground">
                示例：<code className="rounded bg-muted px-1">*/5 * * * *</code> 每5分钟
                <code className="rounded bg-muted px-1">0 9 * * 1-5</code> 工作日9点
                <code className="rounded bg-muted px-1">0 0 1 * *</code> 每月1号零点
                <code className="rounded bg-muted px-1">*/30 * * * *</code> 每30分钟
              </p>
            </Field>
          ) : null}
          {form.config.kind === "once" ? (
            <Field label="触发时间">
              <Input
                value={form.config.fire_at || ""}
                onChange={(e) =>
                  setForm((s) => ({
                    ...s,
                    config: { ...s.config, fire_at: e.target.value },
                  }))
                }
                placeholder="2026-05-10T15:30:00+08:00"
              />
            </Field>
          ) : null}
          {form.config.kind === "interval" ? (
            <Field label="间隔秒数">
              <Input
                type="number"
                value={form.config.interval_sec || 0}
                onChange={(e) =>
                  setForm((s) => ({
                    ...s,
                    config: {
                      ...s.config,
                      interval_sec: Number(e.target.value || 0),
                    },
                  }))
                }
                placeholder="300"
              />
            </Field>
          ) : null}
        </div>

        <div className="space-y-3 rounded-md border p-3">
          <Field label="动作类型">
            <Select
              value={form.config.action.type}
              onChange={(e) =>
                setForm((s) => ({
                  ...s,
                  config: {
                    ...s.config,
                    action: {
                      ...s.config.action,
                      type: e.target.value as SchedulerRuleConfig["action"]["type"],
                    },
                  },
                }))
              }
            >
              <option value="send_message">发送消息</option>
              <option value="run_command">执行命令</option>
              <option value="call_llm">调用 LLM</option>
            </Select>
          </Field>

          {form.config.action.type === "send_message" ||
          form.config.action.type === "call_llm" ? (
            <Field label="目标聊天 ID">
              <Input
                type="number"
                value={form.config.action.target_chat_id || 0}
                onChange={(e) =>
                  setForm((s) => ({
                    ...s,
                    config: {
                      ...s.config,
                      action: {
                        ...s.config.action,
                        target_chat_id: Number(e.target.value || 0),
                      },
                    },
                  }))
                }
              />
            </Field>
          ) : null}

          {form.config.action.type === "send_message" ? (
            <>
              <Field label="消息内容">
                <Textarea
                  value={form.config.action.text || ""}
                  onChange={(e) =>
                    setForm((s) => ({
                      ...s,
                      config: {
                        ...s.config,
                        action: { ...s.config.action, text: e.target.value },
                      },
                    }))
                  }
                  rows={4}
                />
              </Field>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <Field label="自动删除（秒）">
                  <Input
                    type="number"
                    min={0}
                    max={3600}
                    value={form.config.action.delete_after ?? 0}
                    onChange={(e) =>
                      setForm((s) => ({
                        ...s,
                        config: {
                          ...s.config,
                          action: {
                            ...s.config.action,
                            delete_after: Number(e.target.value) || 0,
                          },
                        },
                      }))
                    }
                    placeholder="0 = 不删除"
                  />
                  <p className="text-xs text-muted-foreground">
                    发送后多少秒自动删除，0 或留空 = 不删除，上限 3600
                  </p>
                </Field>
              </div>
            </>
          ) : null}

          {form.config.action.type === "run_command" ? (
            <Field label="命令">
              <Input
                value={form.config.action.command || ""}
                onChange={(e) =>
                  setForm((s) => ({
                    ...s,
                    config: {
                      ...s.config,
                      action: { ...s.config.action, command: e.target.value },
                    },
                  }))
                }
                placeholder=",ai 今天天气"
              />
            </Field>
          ) : null}

          {form.config.action.type === "call_llm" ? (
            <>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <Field label="服务商 ID">
                  <Input
                    type="number"
                    value={form.config.action.provider_id || 0}
                    onChange={(e) =>
                      setForm((s) => ({
                        ...s,
                        config: {
                          ...s.config,
                          action: {
                            ...s.config.action,
                            provider_id: Number(e.target.value || 0),
                          },
                        },
                      }))
                    }
                  />
                </Field>
                <Field label="最大 Token 数">
                  <Input
                    type="number"
                    value={form.config.action.max_tokens || 256}
                    onChange={(e) =>
                      setForm((s) => ({
                        ...s,
                        config: {
                          ...s.config,
                          action: {
                            ...s.config.action,
                            max_tokens: Number(e.target.value || 0),
                          },
                        },
                      }))
                    }
                  />
                </Field>
              </div>
              <Field label="系统提示词">
                <Textarea
                  value={form.config.action.system_prompt || ""}
                  onChange={(e) =>
                    setForm((s) => ({
                      ...s,
                      config: {
                        ...s.config,
                        action: { ...s.config.action, system_prompt: e.target.value },
                      },
                    }))
                  }
                  rows={2}
                />
              </Field>
              <Field label="提示词">
                <Textarea
                  value={form.config.action.prompt || ""}
                  onChange={(e) =>
                    setForm((s) => ({
                      ...s,
                      config: {
                        ...s.config,
                        action: { ...s.config.action, prompt: e.target.value },
                      },
                    }))
                  }
                  rows={4}
                />
              </Field>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <Field label="自动删除（秒）">
                  <Input
                    type="number"
                    min={0}
                    max={3600}
                    value={form.config.action.delete_after ?? 0}
                    onChange={(e) =>
                      setForm((s) => ({
                        ...s,
                        config: {
                          ...s.config,
                          action: {
                            ...s.config.action,
                            delete_after: Number(e.target.value) || 0,
                          },
                        },
                      }))
                    }
                    placeholder="0 = 不删除"
                  />
                  <p className="text-xs text-muted-foreground">
                    发送后多少秒自动删除，0 或留空 = 不删除，上限 3600
                  </p>
                </Field>
              </div>
            </>
          ) : null}
        </div>
      </RuleEditDialogShell>

      <Dialog open={dryOpen} onOpenChange={setDryOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>试运行</DialogTitle>
            <DialogDescription>{dryRule ? `规则：${dryRule.name}` : ""}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <Button
              onClick={handleDryRun}
              disabled={!dryRule || crud.dryRunPending}
            >
              {crud.dryRunPending ? "运行中..." : "执行试运行"}
            </Button>
            {dryResult ? (
              <>
                <div className="rounded-md border p-3 space-y-1">
                  <div>
                    匹配：<b>{dryResult.matched ? "是" : "否"}</b>
                  </div>
                  {dryResult.output && (
                    <div className="text-xs text-muted-foreground">{dryResult.output}</div>
                  )}
                </div>
                <DryRunDetail detail={dryResult.detail} />
              </>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={execOpen} onOpenChange={setExecOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>立即执行</DialogTitle>
            <DialogDescription>
              {execRule ? `规则：${execRule.name} — 将真实发送消息/执行动作` : ""}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <Button onClick={() => execMut.mutate()} disabled={!execRule || execMut.isPending}>
              {execMut.isPending ? "执行中..." : "立即执行"}
            </Button>
            {execResult ? (
              <div className="rounded-md border p-3 space-y-1">
                <div>
                  结果：
                  <b
                    className={
                      execResult.ok
                        ? "text-emerald-600 dark:text-emerald-300"
                        : "text-destructive"
                    }
                  >
                    {execResult.ok ? "成功" : "失败"}
                  </b>
                </div>
                {execResult.error && (
                  <div className="text-xs text-destructive">错误：{execResult.error}</div>
                )}
              </div>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function triggerLabel(cfg: SchedulerRuleConfig): string {
  if (cfg.kind === "once") return `单次 @ ${cfg.fire_at || "-"}`;
  if (cfg.kind === "interval") return `每 ${cfg.interval_sec || 0} 秒`;
  return cfg.cron || "(无效 cron)";
}

const ACTION_TYPE_LABELS: Record<string, string> = {
  send_message: "发送消息",
  run_command: "执行命令",
  call_llm: "调用 LLM",
};
