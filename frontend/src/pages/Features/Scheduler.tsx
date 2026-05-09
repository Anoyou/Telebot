import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Pencil, Play, Plus, Trash2, Zap } from "lucide-react";
import { toast } from "sonner";

import { listAccountFeatures, toggleAccountFeature } from "@/api/accounts";
import { getSystemSettings } from "@/api/system";
import {
  createRule,
  deleteRule,
  dryRunRule,
  executeRule,
  listRules,
  updateRule,
} from "@/api/features";
import type { RuleDryRunResponse, RuleExecuteResponse, RuleOut, SchedulerRuleConfig } from "@/api/types";
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
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
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
  const aid = Number(params.aid);
  const nav = useNavigate();
  const qc = useQueryClient();

  const tzQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const tz = tzQ.data?.timezone || "";

  const featuresQ = useQuery({
    queryKey: ["account", aid, "features"],
    queryFn: () => listAccountFeatures(aid),
    enabled: !!aid,
  });
  const featureEnabled = !!featuresQ.data?.find((x) => x.feature_key === "scheduler")?.enabled;

  const rulesQ = useQuery({
    queryKey: ["account", aid, "rules", "scheduler"],
    queryFn: () => listRules(aid, "scheduler"),
    enabled: !!aid,
  });

  const featureToggleMut = useMutation({
    mutationFn: (next: boolean) => toggleAccountFeature(aid, "scheduler", next),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", aid, "features"] });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

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

  function buildPayload() {
    return {
      name: form.name.trim(),
      enabled: form.enabled,
      priority: form.priority,
      config: form.config as unknown as Record<string, unknown>,
    };
  }

  const saveMut = useMutation({
    mutationFn: async () => {
      const payload = buildPayload();
      if (!payload.name) throw new Error("规则名称必填");
      const cfg = payload.config as unknown as SchedulerRuleConfig;
      if (cfg.kind === "cron" && !(cfg.cron || "").trim()) throw new Error("cron 表达式必填");
      if (cfg.kind === "once" && !(cfg.fire_at || "").trim()) throw new Error("once 模式 fire_at 必填");
      if (cfg.kind === "interval" && Number(cfg.interval_sec || 0) <= 0) throw new Error("interval_sec 必须 > 0");
      if (!cfg.action?.type) throw new Error("action.type 必填");
      if (["send_message", "call_llm"].includes(cfg.action.type) && !cfg.action.target_chat_id) {
        throw new Error("target_chat_id 必填");
      }
      if (cfg.action.type === "send_message" && !(cfg.action.text || "").trim()) {
        throw new Error("send_message 的 text 必填");
      }
      if (cfg.action.type === "run_command" && !(cfg.action.command || cfg.action.text || "").trim()) {
        throw new Error("run_command 的 command 必填");
      }
      if (cfg.action.type === "call_llm") {
        if (!cfg.action.provider_id) throw new Error("call_llm 的 provider_id 必填");
        if (!(cfg.action.prompt || "").trim()) throw new Error("call_llm 的 prompt 必填");
      }
      if (cfg.action.delete_after != null && cfg.action.delete_after > 3600) {
        throw new Error("delete_after 上限为 3600 秒");
      }

      if (!editing) await createRule(aid, "scheduler", payload);
      else await updateRule(aid, "scheduler", editing.id, payload);
    },
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "scheduler"] });
      setEditOpen(false);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: (rid: number) => deleteRule(aid, "scheduler", rid),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "scheduler"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const [dryOpen, setDryOpen] = useState(false);
  const [dryRule, setDryRule] = useState<RuleOut | null>(null);
  const [dryResult, setDryResult] = useState<RuleDryRunResponse | null>(null);

  function openDryRun(rule: RuleOut) {
    setDryRule(rule);
    setDryResult(null);
    setDryOpen(true);
  }

  const dryMut = useMutation({
    mutationFn: () =>
      dryRunRule(aid, "scheduler", dryRule!.id, {
        sample_message: "scheduler dry-run",
        sample_chat_type: "private",
      }),
    onSuccess: (res) => setDryResult(res),
    onError: (err) => toast.error(getErrMsg(err)),
  });

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
        qc.invalidateQueries({ queryKey: ["account", aid, "rules", "scheduler"] });
      }
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (!aid) return <p>账号 ID 不合法</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => nav(`/accounts/${aid}?tab=features`)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回账号
        </Button>
        <h1 className="text-2xl font-semibold tracking-tight">定时任务配置 · #{aid}</h1>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">功能总开关</CardTitle>
              <CardDescription>关闭后本账号所有 scheduler 规则不会触发</CardDescription>
            </div>
            <Switch checked={featureEnabled} onCheckedChange={(v) => featureToggleMut.mutate(v)} />
          </div>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">规则</CardTitle>
              <CardDescription>支持 cron 定时 / once 单次 / interval 间隔，触发动作：发送消息 / 执行命令 / 调用 LLM</CardDescription>
            </div>
            <Button onClick={openCreate}><Plus className="mr-1 h-4 w-4" /> 新建规则</Button>
          </div>
        </CardHeader>
        <CardContent>
          {rulesQ.isLoading ? (
            <div className="flex h-20 items-center justify-center"><Spinner className="text-primary" /></div>
          ) : rulesQ.data && rulesQ.data.length > 0 ? (
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
                {rulesQ.data.map((r) => {
                  const cfg = readConfig(r.config);
                  return (
                    <TableRow key={r.id}>
                      <TableCell className="font-medium">{r.name}</TableCell>
                      <TableCell><Badge variant={r.enabled ? "success" : "secondary"}>{r.enabled ? "ON" : "OFF"}</Badge></TableCell>
                      <TableCell>{r.priority}</TableCell>
                      <TableCell>{triggerLabel(cfg)}</TableCell>
                      <TableCell>
                        <div className="flex flex-col gap-0.5">
                          <span>{ACTION_TYPE_LABELS[cfg.action?.type || "send_message"] || cfg.action?.type}</span>
                          {cfg.action?.delete_after ? (
                            <span className="text-xs text-muted-foreground">自动删除: {cfg.action.delete_after}s</span>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell className="text-xs font-mono">{formatDateTime(cfg.next_fire, tz)}</TableCell>
                      <TableCell className="text-xs font-mono">{formatDateTime(cfg.last_fire, tz)}</TableCell>
                      <TableCell>
                        {cfg.last_result ? (
                          <div className="flex flex-col gap-0.5">
                            <Badge variant={cfg.last_result === "ok" ? "success" : "destructive"}>
                              {cfg.last_result === "ok" ? "成功" : "失败"}
                            </Badge>
                            {cfg.last_error ? (
                              <span className="max-w-[160px] truncate text-xs text-destructive" title={cfg.last_error}>
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
                          <Button size="sm" variant="ghost" onClick={() => openEdit(r)}><Pencil className="mr-1 h-3.5 w-3.5" /> 编辑</Button>
                          <Button size="sm" variant="ghost" onClick={() => openDryRun(r)}><Play className="mr-1 h-3.5 w-3.5" /> 试运行</Button>
                          <Button size="sm" variant="ghost" onClick={() => openExec(r)}><Zap className="mr-1 h-3.5 w-3.5" /> 执行</Button>
                          <Button size="sm" variant="ghost" className="text-destructive" onClick={() => { if (confirm(`删除规则 ${r.name}？`)) delMut.mutate(r.id); }}>
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
            <p className="text-sm text-muted-foreground">暂无规则，点击“新建规则”。</p>
          )}
        </CardContent>
      </Card>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑规则" : "新建规则"}</DialogTitle>
            <DialogDescription>保存后由 worker 热更新，无需重启。</DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div className="space-y-1 md:col-span-2">
              <Label>规则名称</Label>
              <Input value={form.name} onChange={(e) => setForm((s) => ({ ...s, name: e.target.value }))} />
            </div>
            <div className="space-y-1">
              <Label>优先级</Label>
              <Input type="number" value={form.priority} onChange={(e) => setForm((s) => ({ ...s, priority: Number(e.target.value || 0) }))} />
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Switch checked={form.enabled} onCheckedChange={(v) => setForm((s) => ({ ...s, enabled: v }))} />
            <Label>规则启用</Label>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div className="space-y-1">
              <Label>触发类型</Label>
              <Select
                value={form.config.kind}
                onChange={(e) =>
                  setForm((s) => ({
                    ...s,
                    config: { ...s.config, kind: e.target.value as SchedulerRuleConfig["kind"] },
                  }))
                }
              >
              <option value="cron">cron 定时</option>
              <option value="once">once 单次</option>
              <option value="interval">interval 间隔</option>
              </Select>
            </div>
            {form.config.kind === "cron" ? (
              <div className="space-y-1">
                <Label>cron 表达式</Label>
                <Input value={form.config.cron || ""} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, cron: e.target.value } }))} placeholder="*/5 * * * *" />
                <p className="text-xs text-muted-foreground">
                  示例：<code className="rounded bg-muted px-1">*/5 * * * *</code> 每5分钟 
                  <code className="rounded bg-muted px-1">0 9 * * 1-5</code> 工作日9点 
                  <code className="rounded bg-muted px-1">0 0 1 * *</code> 每月1号零点 
                  <code className="rounded bg-muted px-1">*/30 * * * *</code> 每30分钟
                </p>
              </div>
            ) : null}
            {form.config.kind === "once" ? (
              <div className="space-y-1">
                <Label>触发时间</Label>
                <Input value={form.config.fire_at || ""} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, fire_at: e.target.value } }))} placeholder="2026-05-10T15:30:00+08:00" />
              </div>
            ) : null}
            {form.config.kind === "interval" ? (
              <div className="space-y-1">
                <Label>间隔秒数</Label>
                <Input type="number" value={form.config.interval_sec || 0} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, interval_sec: Number(e.target.value || 0) } }))} placeholder="300" />
              </div>
            ) : null}
          </div>

          <div className="space-y-3 rounded-md border p-3">
            <Label>动作类型</Label>
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

            {(form.config.action.type === "send_message" || form.config.action.type === "call_llm") ? (
              <div className="space-y-1">
                <Label>目标聊天 ID</Label>
                <Input type="number" value={form.config.action.target_chat_id || 0} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, action: { ...s.config.action, target_chat_id: Number(e.target.value || 0) } } }))} />
              </div>
            ) : null}

            {form.config.action.type === "send_message" ? (
              <>
                <div className="space-y-1">
                  <Label>消息内容</Label>
                  <Textarea value={form.config.action.text || ""} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, action: { ...s.config.action, text: e.target.value } } }))} rows={4} />
                </div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <div className="space-y-1">
                    <Label>自动删除（秒）</Label>
                    <Input type="number" min={0} max={3600} value={form.config.action.delete_after ?? 0} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, action: { ...s.config.action, delete_after: Number(e.target.value) || 0 } } }))} placeholder="0 = 不删除" />
                    <p className="text-xs text-muted-foreground">发送后多少秒自动删除，0 或留空 = 不删除，上限 3600</p>
                  </div>
                </div>
              </>
            ) : null}

            {form.config.action.type === "run_command" ? (
              <div className="space-y-1">
                <Label>命令</Label>
                <Input value={form.config.action.command || ""} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, action: { ...s.config.action, command: e.target.value } } }))} placeholder=",ai 今天天气" />
              </div>
            ) : null}

            {form.config.action.type === "call_llm" ? (
              <>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <div className="space-y-1">
                    <Label>服务商 ID</Label>
                    <Input type="number" value={form.config.action.provider_id || 0} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, action: { ...s.config.action, provider_id: Number(e.target.value || 0) } } }))} />
                  </div>
                  <div className="space-y-1">
                    <Label>最大 Token 数</Label>
                    <Input type="number" value={form.config.action.max_tokens || 256} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, action: { ...s.config.action, max_tokens: Number(e.target.value || 0) } } }))} />
                  </div>
                </div>
                <div className="space-y-1">
                  <Label>系统提示词</Label>
                  <Textarea value={form.config.action.system_prompt || ""} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, action: { ...s.config.action, system_prompt: e.target.value } } }))} rows={2} />
                </div>
                <div className="space-y-1">
                  <Label>提示词</Label>
                  <Textarea value={form.config.action.prompt || ""} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, action: { ...s.config.action, prompt: e.target.value } } }))} rows={4} />
                </div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <div className="space-y-1">
                    <Label>自动删除（秒）</Label>
                    <Input type="number" min={0} max={3600} value={form.config.action.delete_after ?? 0} onChange={(e) => setForm((s) => ({ ...s, config: { ...s.config, action: { ...s.config.action, delete_after: Number(e.target.value) || 0 } } }))} placeholder="0 = 不删除" />
                    <p className="text-xs text-muted-foreground">发送后多少秒自动删除，0 或留空 = 不删除，上限 3600</p>
                  </div>
                </div>
              </>
            ) : null}
          </div>

          <DialogFooter>
            <Button variant="secondary" onClick={() => setEditOpen(false)}>取消</Button>
            <Button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>{saveMut.isPending ? "保存中..." : "保存"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={dryOpen} onOpenChange={setDryOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>试运行</DialogTitle>
            <DialogDescription>{dryRule ? `规则：${dryRule.name}` : ""}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <Button onClick={() => dryMut.mutate()} disabled={!dryRule || dryMut.isPending}>{dryMut.isPending ? "运行中..." : "执行试运行"}</Button>
            {dryResult ? (
              <>
                <div className="rounded-md border p-3 space-y-1">
                  <div>匹配：<b>{dryResult.matched ? "是" : "否"}</b></div>
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
            <DialogDescription>{execRule ? `规则：${execRule.name} — 将真实发送消息/执行动作` : ""}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <Button onClick={() => execMut.mutate()} disabled={!execRule || execMut.isPending}>{execMut.isPending ? "执行中..." : "立即执行"}</Button>
            {execResult ? (
              <div className="rounded-md border p-3 space-y-1">
                <div>结果：<b className={execResult.ok ? "text-green-600" : "text-destructive"}>{execResult.ok ? "成功" : "失败"}</b></div>
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
