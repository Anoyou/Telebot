// 自动复读配置：列出该账号的 autorepeat rule，CRUD + 试运行
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, Pencil, Trash2, Play } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
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
import { Spinner } from "@/components/ui/misc";
import {
  createRule,
  deleteRule,
  dryRunRule,
  listRules,
  updateRule,
} from "@/api/features";
import { listAccountFeatures, toggleAccountFeature } from "@/api/accounts";
import { getErrMsg } from "@/lib/api";
import type {
  AutorepeatRuleConfig,
  RuleDryRunResponse,
  RuleOut,
} from "@/api/types";
import { DryRunDetail } from "@/components/DryRunDetail";

// rule.config 默认值
function defaultConfig(): AutorepeatRuleConfig {
  return {
    target_chat_id: 0,
    time_window: 300,
    min_users: 5,
  };
}

function readConfig(c: Record<string, unknown> | undefined): AutorepeatRuleConfig {
  const def = defaultConfig();
  if (!c) return def;
  return { ...def, ...(c as Partial<AutorepeatRuleConfig>) };
}

interface FormState {
  name: string;
  enabled: boolean;
  priority: number;
  config: AutorepeatRuleConfig;
}

function emptyForm(): FormState {
  return { name: "", enabled: true, priority: 100, config: defaultConfig() };
}

export function AutorepeatConfig() {
  const params = useParams();
  const aid = Number(params.aid);
  const nav = useNavigate();
  const qc = useQueryClient();

  const featuresQ = useQuery({
    queryKey: ["account", aid, "features"],
    queryFn: () => listAccountFeatures(aid),
    enabled: !!aid,
  });
  const featureItem = featuresQ.data?.find((x) => x.feature_key === "autorepeat");
  const featureEnabled = !!featureItem?.enabled;

  const rulesQ = useQuery({
    queryKey: ["account", aid, "rules", "autorepeat"],
    queryFn: () => listRules(aid, "autorepeat"),
    enabled: !!aid,
  });

  const featureToggleMut = useMutation({
    mutationFn: (next: boolean) => toggleAccountFeature(aid, "autorepeat", next),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", aid, "features"] });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // ===================== 编辑/新建 Dialog =====================
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
    const cfg = readConfig(r.config);
    setForm({
      name: r.name,
      enabled: r.enabled,
      priority: r.priority,
      config: cfg,
    });
    setEditOpen(true);
  }

  function buildPayload() {
    return {
      name: form.name.trim(),
      enabled: form.enabled,
      priority: form.priority,
      config: {
        ...form.config,
        target_chat_id: Number(form.config.target_chat_id) || 0,
        time_window: Number(form.config.time_window) || 300,
        min_users: Number(form.config.min_users) || 5,
      } as Record<string, unknown>,
    };
  }

  const saveMut = useMutation({
    mutationFn: async () => {
      const payload = buildPayload();
      if (!payload.name) throw new Error("规则名称必填");
      if (!payload.config.target_chat_id) throw new Error("群组 ID 必填");
      if (!editing) {
        await createRule(aid, "autorepeat", payload);
      } else {
        await updateRule(aid, "autorepeat", editing.id, payload);
      }
    },
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "autorepeat"] });
      setEditOpen(false);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: (rid: number) => deleteRule(aid, "autorepeat", rid),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "autorepeat"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // ===================== 试运行 Dialog =====================
  const [dryOpen, setDryOpen] = useState(false);
  const [dryRule, setDryRule] = useState<RuleOut | null>(null);
  const [drySample, setDrySample] = useState("");
  const [dryChatId, setDryChatId] = useState("");
  const [dryResult, setDryResult] = useState<RuleDryRunResponse | null>(null);

  function openDryRun(rule: RuleOut) {
    setDryRule(rule);
    setDrySample("");
    setDryResult(null);
    const cfg = readConfig(rule.config);
    setDryChatId(cfg.target_chat_id ? String(cfg.target_chat_id) : "");
    setDryOpen(true);
  }

  const dryMut = useMutation({
    mutationFn: () =>
      dryRunRule(aid, "autorepeat", dryRule!.id, {
        sample_message: drySample,
        sample_chat_type: "group",
        sample_chat_id: dryChatId ? Number(dryChatId) : undefined,
      }),
    onSuccess: (res) => setDryResult(res),
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (!aid) return <p>账号 ID 不合法</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => nav(`/accounts/${aid}?tab=features`)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回账号
        </Button>
        <h1 className="text-2xl font-semibold tracking-tight">
          自动复读配置 · #{aid}
        </h1>
      </div>

      {/* 提示条 */}
      <div className="rounded-md border border-blue-200 bg-blue-50/60 px-3 py-2 text-xs text-blue-800 space-y-1">
        <div>✅ 保存后立即生效，无需重启 worker。</div>
        <div>
          📋 每条规则对应一个群组的复读配置。当 <b>指定时间内</b> 有 <b>指定人数</b> 的不同用户发送
          完全相同的内容时，自动复读该内容。
        </div>
        <div>
          🔁 同一内容同群每天只复读一次（UTC+8 0点重置）。匿名消息、非文本消息、自己发送的消息、机器人消息会被忽略。
        </div>
      </div>

      {/* 总开关 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">功能总开关</CardTitle>
              <CardDescription>
                关闭后所有规则都不会触发；启用即生效
              </CardDescription>
            </div>
            <Switch
              checked={featureEnabled}
              onCheckedChange={(v) => featureToggleMut.mutate(v)}
            />
          </div>
        </CardHeader>
      </Card>

      {/* 规则列表 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">规则</CardTitle>
              <CardDescription>每条规则对应一个群组的复读配置</CardDescription>
            </div>
            <Button onClick={openCreate}>
              <Plus className="mr-1 h-4 w-4" /> 新建规则
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {rulesQ.isLoading ? (
            <div className="flex h-20 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : rulesQ.data && rulesQ.data.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>启用</TableHead>
                  <TableHead>优先级</TableHead>
                  <TableHead>群组 ID</TableHead>
                  <TableHead>触发条件</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rulesQ.data.map((r) => {
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
                      <TableCell>
                        <code className="text-xs">{cfg.target_chat_id || "—"}</code>
                      </TableCell>
                      <TableCell>
                        {cfg.time_window ?? 300}秒 / {cfg.min_users ?? 5}人
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="inline-flex gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => openEdit(r)}
                          >
                            <Pencil className="mr-1 h-3.5 w-3.5" /> 编辑
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => openDryRun(r)}
                          >
                            <Play className="mr-1 h-3.5 w-3.5" /> 试运行
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-destructive"
                            onClick={() => {
                              if (confirm(`删除规则 ${r.name}？`))
                                delMut.mutate(r.id);
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
            <p className="py-8 text-center text-sm text-muted-foreground">
              暂无规则，点击右上角「新建规则」
            </p>
          )}
        </CardContent>
      </Card>

      {/* 编辑 / 新建 */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑规则" : "新建规则"}</DialogTitle>
            <DialogDescription>
              配置一个群组的自动复读参数
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <Field label="名称">
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="例：技术交流群"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="启用">
                <div className="flex h-10 items-center">
                  <Switch
                    checked={form.enabled}
                    onCheckedChange={(v) => setForm({ ...form, enabled: v })}
                  />
                </div>
              </Field>
              <Field label="优先级（数字越大越优先）">
                <Input
                  inputMode="numeric"
                  value={form.priority.toString()}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      priority: Number(e.target.value.replace(/[^0-9]/g, "") || 0),
                    })
                  }
                />
              </Field>
            </div>

            <Field label="群组 ID">
              <Input
                inputMode="numeric"
                value={form.config.target_chat_id ? String(form.config.target_chat_id) : ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: {
                      ...form.config,
                      target_chat_id: Number(e.target.value.replace(/[^0-9-]/g, "") || 0),
                    },
                  })
                }
                placeholder="例：-1001234567890"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Telethon marked ID 格式（超级群组通常为 -100 开头的负数）
              </p>
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="时间窗口（秒）">
                <Input
                  inputMode="numeric"
                  value={(form.config.time_window ?? 300).toString()}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        time_window: Number(e.target.value.replace(/[^0-9]/g, "") || 300),
                      },
                    })
                  }
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  统计相同消息的时间范围，默认 300（5分钟）
                </p>
              </Field>
              <Field label="触发人数">
                <Input
                  inputMode="numeric"
                  value={(form.config.min_users ?? 5).toString()}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        min_users: Number(e.target.value.replace(/[^0-9]/g, "") || 5),
                      },
                    })
                  }
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  发送相同内容的不同用户数，默认 5
                </p>
              </Field>
            </div>
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditOpen(false)}>
              取消
            </Button>
            <Button
              onClick={() => saveMut.mutate()}
              disabled={saveMut.isPending}
            >
              {saveMut.isPending ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 试运行 */}
      <Dialog open={dryOpen} onOpenChange={setDryOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>试运行 · {dryRule?.name}</DialogTitle>
            <DialogDescription>
              输入一条样例消息，验证规则是否命中
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <Field label="样例消息">
              <Input
                value={drySample}
                onChange={(e) => setDrySample(e.target.value)}
                placeholder="输入要测试的文本内容"
              />
            </Field>
            <Field label="样本群 ID">
              <Input
                inputMode="numeric"
                value={dryChatId}
                onChange={(e) =>
                  setDryChatId(e.target.value.replace(/[^0-9-]/g, ""))
                }
                placeholder="例：-1001234567890"
              />
            </Field>

            {dryResult && (
              <>
                <div className="rounded-md border bg-muted/40 p-3 text-xs">
                  <div className="mb-1">
                    命中：
                    <Badge variant={dryResult.matched ? "success" : "secondary"}>
                      {dryResult.matched ? "是" : "否"}
                    </Badge>
                  </div>
                  {dryResult.output != null && (
                    <pre className="whitespace-pre-wrap">{dryResult.output}</pre>
                  )}
                </div>
                <DryRunDetail detail={dryResult.detail} />
              </>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDryOpen(false)}>
              关闭
            </Button>
            <Button
              disabled={!drySample || dryMut.isPending}
              onClick={() => dryMut.mutate()}
            >
              {dryMut.isPending ? "运行中…" : "运行"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      {children}
    </div>
  );
}
