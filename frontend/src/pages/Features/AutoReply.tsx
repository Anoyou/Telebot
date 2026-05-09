// 自动回复配置：列出该账号的 auto_reply rule，CRUD + 试运行
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, Pencil, Trash2, Play } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select } from "@/components/ui/select";
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
  AutoReplyMatch,
  AutoReplyRuleConfig,
  AutoReplyScope,
  RuleDryRunResponse,
  RuleOut,
} from "@/api/types";
import { DryRunDetail } from "@/components/DryRunDetail";

// rule.config 默认值
function defaultConfig(): AutoReplyRuleConfig {
  return {
    match: "keyword",
    patterns: [],
    scope: "private",
    reply: "",
    cooldown_seconds: 0,
    case_sensitive: false,
    reply_to: true,    // 默认以引用形式回复
  };
}

function readConfig(c: Record<string, unknown> | undefined): AutoReplyRuleConfig {
  // 把后端 rule.config 强转为前端类型；缺失字段补默认
  const def = defaultConfig();
  if (!c) return def;
  return { ...def, ...(c as Partial<AutoReplyRuleConfig>) };
}

interface FormState {
  name: string;
  enabled: boolean;
  priority: number;
  config: AutoReplyRuleConfig;
}

function emptyForm(): FormState {
  return { name: "", enabled: true, priority: 100, config: defaultConfig() };
}

export function AutoReplyConfig() {
  const params = useParams();
  const aid = Number(params.aid);
  const nav = useNavigate();
  const qc = useQueryClient();

  const featuresQ = useQuery({
    queryKey: ["account", aid, "features"],
    queryFn: () => listAccountFeatures(aid),
    enabled: !!aid,
  });
  const featureItem = featuresQ.data?.find((x) => x.feature_key === "auto_reply");
  const featureEnabled = !!featureItem?.enabled;

  const rulesQ = useQuery({
    queryKey: ["account", aid, "rules", "auto_reply"],
    queryFn: () => listRules(aid, "auto_reply"),
    enabled: !!aid,
  });

  const featureToggleMut = useMutation({
    mutationFn: (next: boolean) => toggleAccountFeature(aid, "auto_reply", next),
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
  // patterns 文本编辑（一行一条）
  const [patternsText, setPatternsText] = useState("");
  // group_ids 文本编辑（自由编辑，保存时再 split）
  const [groupIdsText, setGroupIdsText] = useState("");

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setPatternsText("");
    setGroupIdsText("");
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
    setPatternsText((cfg.patterns || []).join("\n"));
    setGroupIdsText((cfg.group_ids || []).join("\n"));
    setEditOpen(true);
  }

  function buildPayload() {
    const patterns = patternsText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    const groupIds = groupIdsText
      .split(/[\s,，;；]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    return {
      name: form.name.trim(),
      enabled: form.enabled,
      priority: form.priority,
      config: { ...form.config, patterns, group_ids: groupIds } as Record<
        string,
        unknown
      >,
    };
  }

  const saveMut = useMutation({
    mutationFn: async () => {
      const payload = buildPayload();
      if (!payload.name) throw new Error("规则名称必填");
      if (!editing) {
        await createRule(aid, "auto_reply", payload);
      } else {
        await updateRule(aid, "auto_reply", editing.id, payload);
      }
    },
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "auto_reply"] });
      setEditOpen(false);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: (rid: number) => deleteRule(aid, "auto_reply", rid),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "auto_reply"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // ===================== 试运行 Dialog =====================
  const [dryOpen, setDryOpen] = useState(false);
  const [dryRule, setDryRule] = useState<RuleOut | null>(null);
  const [drySample, setDrySample] = useState("");
  const [dryChat, setDryChat] = useState<"private" | "group">("private");
  const [dryChatId, setDryChatId] = useState("");
  const [dryResult, setDryResult] = useState<RuleDryRunResponse | null>(null);

  // 打开试运行：根据规则 scope 推导默认会话类型 + 默认 chat_id
  function openDryRun(rule: RuleOut) {
    setDryRule(rule);
    setDrySample("");
    setDryResult(null);
    const cfg = (rule.config || {}) as Record<string, unknown>;
    const scope = cfg.scope as string | undefined;
    if (scope === "private") {
      setDryChat("private");
    } else if (scope === "group_all" || scope === "group_specific") {
      setDryChat("group");
    }
    if (scope === "group_specific") {
      const gids = (cfg.group_ids as string[]) || [];
      setDryChatId(gids[0] ?? "");
    } else {
      setDryChatId("");
    }
    setDryOpen(true);
  }

  const dryMut = useMutation({
    mutationFn: () =>
      dryRunRule(aid, "auto_reply", dryRule!.id, {
        sample_message: drySample,
        sample_chat_type: dryChat,
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
          自动回复配置 · #{aid}
        </h1>
      </div>

      {/* 提示条 */}
      <div className="rounded-md border border-blue-200 bg-blue-50/60 px-3 py-2 text-xs text-blue-800 space-y-1">
        <div>✅ 保存后立即生效，无需重启 worker。</div>
        <div>
          ⚠ <b>仅响应别人发来的消息</b>（incoming）。用绑定的 userbot 账号自己发关键词
          <b>不会触发</b>——必须用其他账号在群里 / 私聊里发。
        </div>
        <div>
          🔍 不命中时去「日志中心」筛 source=plugin/worker 的 info
          条，会显示 <code>[event]</code> 收到了什么、<code>[auto_reply]</code> 跳过的具体原因。
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
              <CardDescription>支持关键词与正则；按优先级排序</CardDescription>
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
                  <TableHead>匹配</TableHead>
                  <TableHead>作用范围</TableHead>
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
                        {cfg.match === "regex" ? "正则" : "关键词"}（
                        {cfg.patterns?.length ?? 0}）
                      </TableCell>
                      <TableCell>{scopeLabel(cfg.scope)}</TableCell>
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
              支持变量：{"{sender}"} {"{chat}"} {"{text}"}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <Field label="名称">
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
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
              <Field label="匹配类型">
                <Select
                  value={form.config.match}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        match: e.target.value as AutoReplyMatch,
                      },
                    })
                  }
                >
                  <option value="keyword">关键词</option>
                  <option value="regex">正则</option>
                </Select>
              </Field>
              <Field label="作用范围">
                <Select
                  value={form.config.scope}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        scope: e.target.value as AutoReplyScope,
                      },
                    })
                  }
                >
                  <option value="private">仅私聊</option>
                  <option value="group_all">所有群</option>
                  <option value="group_specific">指定群</option>
                </Select>
              </Field>
            </div>

            {form.config.scope === "group_specific" && (
              <Field label="指定群 ID（每行一个，或用空格 / 逗号分隔）">
                <Textarea
                  rows={4}
                  placeholder={
                    "支持以下任一格式：\n" +
                    "  -1001234567890   （Telethon 内部 id）\n" +
                    "  1234567890       （从 t.me/c/<id> 复制）\n" +
                    "  -1234567890      （basic group id）"
                  }
                  value={groupIdsText}
                  onChange={(e) => setGroupIdsText(e.target.value)}
                />
              </Field>
            )}

            <Field label="模式（每行一条）">
              <Textarea
                rows={4}
                value={patternsText}
                onChange={(e) => setPatternsText(e.target.value)}
                placeholder={
                  form.config.match === "regex"
                    ? "例：^/start.*$"
                    : "例：你好\n在吗"
                }
              />
            </Field>

            <Field label="回复内容">
              <Textarea
                rows={3}
                value={form.config.reply}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: { ...form.config, reply: e.target.value },
                  })
                }
                placeholder="支持变量：{sender}、{chat}、{text}"
              />
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="冷却秒数">
                <Input
                  inputMode="numeric"
                  value={(form.config.cooldown_seconds ?? 0).toString()}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        cooldown_seconds: Number(
                          e.target.value.replace(/[^0-9]/g, "") || 0,
                        ),
                      },
                    })
                  }
                />
              </Field>
              <Field label="区分大小写">
                <div className="flex h-10 items-center">
                  <Switch
                    checked={!!form.config.case_sensitive}
                    onCheckedChange={(v) =>
                      setForm({
                        ...form,
                        config: { ...form.config, case_sensitive: v },
                      })
                    }
                  />
                </div>
              </Field>
              <Field label="以「引用」形式回复">
                <div className="flex h-10 items-center gap-2">
                  <Switch
                    checked={form.config.reply_to !== false}
                    onCheckedChange={(v) =>
                      setForm({
                        ...form,
                        config: { ...form.config, reply_to: v },
                      })
                    }
                  />
                  <span className="text-xs text-muted-foreground">
                    开 = 引用触发消息；关 = 发新消息
                  </span>
                </div>
              </Field>
              <Field label="白名单（每行一个 user_id，可选）">
                <Textarea
                  rows={2}
                  value={(form.config.whitelist || []).join("\n")}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        whitelist: e.target.value
                          .split("\n")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      },
                    })
                  }
                />
              </Field>
              <Field label="黑名单（每行一个 user_id，可选）">
                <Textarea
                  rows={2}
                  value={(form.config.blacklist || []).join("\n")}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        blacklist: e.target.value
                          .split("\n")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      },
                    })
                  }
                />
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
              输入一条样例消息，验证规则是否命中、回复内容
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <Field label="样例消息">
              <Textarea
                rows={3}
                value={drySample}
                onChange={(e) => setDrySample(e.target.value)}
              />
            </Field>
            <Field label="会话类型">
              <Select
                value={dryChat}
                onChange={(e) =>
                  setDryChat(e.target.value as "private" | "group")
                }
              >
                <option value="private">私聊</option>
                <option value="group">群聊</option>
              </Select>
            </Field>
            {dryChat === "group" && (
              <Field label="样本群 ID（可选；留空 = 任意群，scope=group_specific 时自动取规则中第一项）">
                <Input
                  inputMode="numeric"
                  placeholder="例：-1001234567890 或 1234567890"
                  value={dryChatId}
                  onChange={(e) =>
                    setDryChatId(e.target.value.replace(/[^0-9-]/g, ""))
                  }
                />
              </Field>
            )}

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

function scopeLabel(s: AutoReplyScope): string {
  switch (s) {
    case "private":
      return "私聊";
    case "group_all":
      return "全部群";
    case "group_specific":
      return "指定群";
    default:
      return s;
  }
}
