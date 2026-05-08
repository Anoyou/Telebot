// 转发规则配置：列出该账号的 forward rule，CRUD + 试运行
//
// 与 AutoReply.tsx 结构保持一致（左侧规则表 + 右侧编辑 Dialog + 底部 dry-run）。
// rule.config 的字段语义见 backend/app/worker/plugins/builtin/forward/manifest.py。
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
  ForwardMode,
  ForwardRuleConfig,
  ForwardSourceKind,
  RuleOut,
} from "@/api/types";

// rule.config 默认值（新建规则时用）
function defaultConfig(): ForwardRuleConfig {
  return {
    source_kind: "all",
    source_peers: [],
    keyword: "",
    target_chat_id: 0,
    mode: "forward_native",
    include_media: true,
    header: "",
  };
}

function readConfig(c: Record<string, unknown> | undefined): ForwardRuleConfig {
  // 把后端 rule.config 强转为前端类型；缺失字段补默认
  const def = defaultConfig();
  if (!c) return def;
  return { ...def, ...(c as Partial<ForwardRuleConfig>) };
}

interface FormState {
  name: string;
  enabled: boolean;
  priority: number;
  config: ForwardRuleConfig;
}

function emptyForm(): FormState {
  return { name: "", enabled: true, priority: 100, config: defaultConfig() };
}

export function ForwardConfig() {
  const params = useParams();
  const aid = Number(params.aid);
  const nav = useNavigate();
  const qc = useQueryClient();

  const featuresQ = useQuery({
    queryKey: ["account", aid, "features"],
    queryFn: () => listAccountFeatures(aid),
    enabled: !!aid,
  });
  const featureItem = featuresQ.data?.find(
    (x) => x.feature_key === "forward",
  );
  const featureEnabled = !!featureItem?.enabled;

  const rulesQ = useQuery({
    queryKey: ["account", aid, "rules", "forward"],
    queryFn: () => listRules(aid, "forward"),
    enabled: !!aid,
  });

  const featureToggleMut = useMutation({
    mutationFn: (next: boolean) => toggleAccountFeature(aid, "forward", next),
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
  // source_peers 用独立 state 文本编辑，避免 #3 提到的「换行 bug」
  // 保存时再 split + 转 int
  const [peersText, setPeersText] = useState("");
  // target_chat_id 同理：先用文本编辑，保存时转 number
  const [targetText, setTargetText] = useState("");

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setPeersText("");
    setTargetText("");
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
    setPeersText((cfg.source_peers || []).map(String).join("\n"));
    setTargetText(cfg.target_chat_id ? String(cfg.target_chat_id) : "");
    setEditOpen(true);
  }

  function buildPayload() {
    // peersText：每行 / 逗号 / 分号 分隔；忽略解析失败项
    const peers = peersText
      .split(/[\s,，;；]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => Number(s))
      .filter((n) => Number.isFinite(n));
    const target = Number(targetText.trim());
    return {
      name: form.name.trim(),
      enabled: form.enabled,
      priority: form.priority,
      config: {
        ...form.config,
        source_peers: peers,
        target_chat_id: Number.isFinite(target) ? target : 0,
      } as Record<string, unknown>,
    };
  }

  const saveMut = useMutation({
    mutationFn: async () => {
      const payload = buildPayload();
      if (!payload.name) throw new Error("规则名称必填");
      // payload.config 是 Record<string, unknown>，先 cast 到 unknown 再到 ForwardRuleConfig
      // 才能通过 strict TS 检查（同类型断言两步走）
      const cfg = payload.config as unknown as ForwardRuleConfig;
      if (cfg.source_kind === "keyword" && !(cfg.keyword || "").trim())
        throw new Error("关键词模式下 keyword 不能为空");
      if (cfg.source_kind === "peers" && !(cfg.source_peers?.length ?? 0))
        throw new Error("peers 模式下至少填一个 chat_id");
      if (!editing) {
        await createRule(aid, "forward", payload);
      } else {
        await updateRule(aid, "forward", editing.id, payload);
      }
    },
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "forward"] });
      setEditOpen(false);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: (rid: number) => deleteRule(aid, "forward", rid),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "forward"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // ===================== 试运行 Dialog =====================
  const [dryOpen, setDryOpen] = useState(false);
  const [dryRule, setDryRule] = useState<RuleOut | null>(null);
  const [drySample, setDrySample] = useState("");
  const [dryChatId, setDryChatId] = useState("");
  const [dryResult, setDryResult] = useState<{
    matched: boolean;
    output?: string | null;
  } | null>(null);

  function openDryRun(rule: RuleOut) {
    setDryRule(rule);
    setDrySample("");
    setDryResult(null);
    const cfg = readConfig(rule.config);
    // peers 模式下默认带上第一个 chat_id 作样本，方便看到命中
    if (cfg.source_kind === "peers" && (cfg.source_peers || []).length) {
      setDryChatId(String(cfg.source_peers![0]));
    } else {
      setDryChatId("");
    }
    setDryOpen(true);
  }

  const dryMut = useMutation({
    mutationFn: () =>
      dryRunRule(aid, "forward", dryRule!.id, {
        sample_message: drySample,
        // forward 不区分 chat type，固定 group 即可（后端只看 source_kind）
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
          消息转发配置 · #{aid}
        </h1>
      </div>

      {/* 提示条 */}
      <div className="rounded-md border border-blue-200 bg-blue-50/60 px-3 py-2 text-xs text-blue-800 space-y-1">
        <div>✅ 保存后立即生效，无需重启 worker。</div>
        <div>
          ⚠ <b>仅响应别人发来的消息</b>（incoming）。本账号自己发的消息不会被转发。
        </div>
        <div>
          🚦 每条转发都会过风控引擎；触发 FloodWait 会自动 sleep ≤60s
          后重试一次，最终失败仅写日志，不会让 worker 崩溃。
        </div>
      </div>

      {/* 总开关 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">功能总开关</CardTitle>
              <CardDescription>
                关闭后所有转发规则都不会触发；启用即生效
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
              <CardDescription>
                按优先级排序；多条规则可同时命中（一对多）
              </CardDescription>
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
                  <TableHead>源</TableHead>
                  <TableHead>目标</TableHead>
                  <TableHead>方式</TableHead>
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
                      <TableCell>{sourceLabel(cfg)}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {cfg.target_chat_id || <span className="text-muted-foreground">未设置</span>}
                      </TableCell>
                      <TableCell>{modeLabel(cfg.mode)}</TableCell>
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
              "原生转发"显示原作者；"复制 / 引用"不显示；"仅链接"对公开超级群可点
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
            </div>

            <Field label="源筛选">
              <Select
                value={form.config.source_kind}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: {
                      ...form.config,
                      source_kind: e.target.value as ForwardSourceKind,
                    },
                  })
                }
              >
                <option value="all">所有 incoming 消息</option>
                <option value="peers">指定 peer 列表</option>
                <option value="keyword">关键词触发</option>
              </Select>
            </Field>

            {form.config.source_kind === "peers" && (
              <Field label="源 chat_id（每行 / 逗号 / 分号 分隔）">
                <Textarea
                  rows={4}
                  placeholder={
                    "例：\n" +
                    "  -1001234567890\n" +
                    "  1234567890\n" +
                    "  -1234567890"
                  }
                  value={peersText}
                  onChange={(e) => setPeersText(e.target.value)}
                />
              </Field>
            )}

            {form.config.source_kind === "keyword" && (
              <Field label="关键词（不区分大小写；包含即命中）">
                <Input
                  value={form.config.keyword || ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: { ...form.config, keyword: e.target.value },
                    })
                  }
                  placeholder="例：紧急"
                />
              </Field>
            )}

            <Field label="目标 chat_id（可选）">
              <Input
                inputMode="numeric"
                value={targetText}
                onChange={(e) =>
                  setTargetText(e.target.value.replace(/[^0-9-]/g, ""))
                }
                placeholder="留空 = 转发到消息来源的 chat；例：-1001234567890"
              />
            </Field>

            <Field label="转发方式">
              <Select
                value={form.config.mode}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: {
                      ...form.config,
                      mode: e.target.value as ForwardMode,
                    },
                  })
                }
              >
                <option value="forward_native">原生转发（携带原作者）</option>
                <option value="copy_text">复制文本（不显示原作者）</option>
                <option value="quote">引用包装（带"来自 X"前缀）</option>
                <option value="link_only">仅发链接（公开群可点）</option>
              </Select>
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="包含含媒体的消息">
                <div className="flex h-10 items-center gap-2">
                  <Switch
                    checked={form.config.include_media !== false}
                    onCheckedChange={(v) =>
                      setForm({
                        ...form,
                        config: { ...form.config, include_media: v },
                      })
                    }
                  />
                  <span className="text-xs text-muted-foreground">
                    关 = 仅纯文本通过
                  </span>
                </div>
              </Field>
            </div>

            <Field label="固定前缀（copy / quote / link_only 模式生效）">
              <Textarea
                rows={2}
                value={form.config.header || ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: { ...form.config, header: e.target.value },
                  })
                }
                placeholder="例：[团队预警] "
              />
            </Field>
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
              输入一条样例消息，验证 source_kind 是否命中（不会真的下发转发）
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
            <Field label="样本来源 chat_id（peers 模式必填；其它可选）">
              <Input
                inputMode="numeric"
                placeholder="例：-1001234567890"
                value={dryChatId}
                onChange={(e) =>
                  setDryChatId(e.target.value.replace(/[^0-9-]/g, ""))
                }
              />
            </Field>

            {dryResult && (
              <div className="rounded-md border bg-muted/40 p-3 text-xs">
                <div className="mb-1">
                  命中：
                  <Badge variant={dryResult.matched ? "success" : "secondary"}>
                    {dryResult.matched ? "是" : "否"}
                  </Badge>
                </div>
                {dryResult.matched && dryResult.output != null && (
                  <pre className="whitespace-pre-wrap">{dryResult.output}</pre>
                )}
              </div>
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

function sourceLabel(cfg: ForwardRuleConfig): string {
  switch (cfg.source_kind) {
    case "all":
      return "所有 incoming";
    case "peers":
      return `指定 peers (${cfg.source_peers?.length ?? 0})`;
    case "keyword":
      return `关键词「${cfg.keyword || ""}」`;
    default:
      return cfg.source_kind;
  }
}

function modeLabel(m: ForwardMode): string {
  switch (m) {
    case "forward_native":
      return "原生转发";
    case "copy_text":
      return "复制文本";
    case "quote":
      return "引用包装";
    case "link_only":
      return "仅链接";
    default:
      return m;
  }
}
