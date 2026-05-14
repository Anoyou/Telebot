// 转发规则配置：列出该账号的 forward rule，CRUD + 试运行
// rule.config 的字段语义见 backend/app/worker/plugins/builtin/forward/manifest.py。
import { useState } from "react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import { Plus, Pencil, Trash2, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Spinner } from "@/components/ui/misc";
import type {
  ForwardMode,
  ForwardRuleConfig,
  ForwardSourceKind,
  RuleDryRunResponse,
  RuleOut,
} from "@/api/types";
import {
  DryRunDialogShell,
  Field,
  RuleEditDialogShell,
  RuleFeatureToggleCard,
  RuleInfoBox,
  RulePageHeader,
  useRuleCrud,
} from "./_shared";

function defaultConfig(): ForwardRuleConfig {
  return {
    source_kind: "all",
    source_peers: [],
    keyword: "",
    duplicate_window: 60,
    duplicate_threshold: 3,
    target_chat_id: 0,
    mode: "forward_native",
    include_media: true,
    header: "",
  };
}

function readConfig(c: Record<string, unknown> | undefined): ForwardRuleConfig {
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

  const crud = useRuleCrud({
    aid,
    ruleKind: "forward",
    featureKey: "forward",
  });

  // ===================== 编辑/新建 Dialog =====================
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<RuleOut | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm());
  // source_peers / target_chat_id 用独立文本 state（避免输入"-"时被 Number() 吃掉）
  const [peersText, setPeersText] = useState("");
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

  async function handleSave() {
    const peers = peersText
      .split(/[\s,，;；]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => Number(s))
      .filter((n) => Number.isFinite(n));
    const target = Number(targetText.trim());
    const cfg: Record<string, unknown> = {
      ...form.config,
      source_peers: peers,
      target_chat_id: Number.isFinite(target) ? target : 0,
    };
    const payload = {
      name: form.name.trim(),
      enabled: form.enabled,
      priority: form.priority,
      config: cfg,
    };
    if (!payload.name) {
      toast.error("规则名称必填");
      return;
    }
    // 子模式校验
    const c = cfg as unknown as ForwardRuleConfig;
    if (c.source_kind === "keyword" && !(c.keyword || "").trim()) {
      toast.error("关键词模式下 keyword 不能为空");
      return;
    }
    if (c.source_kind === "peers" && !(c.source_peers?.length ?? 0)) {
      toast.error("peers 模式下至少填一个 chat_id");
      return;
    }
    if (c.source_kind === "duplicate") {
      if ((c.duplicate_window ?? 0) <= 0) {
        toast.error("duplicate 模式下时间窗口必须 > 0");
        return;
      }
      if ((c.duplicate_threshold ?? 0) < 2) {
        toast.error("duplicate 模式下不同用户数阈值至少为 2");
        return;
      }
    }
    await crud.saveRule({
      editing,
      payload,
      onSuccess: () => setEditOpen(false),
    });
  }

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
    if (cfg.source_kind === "peers" && (cfg.source_peers || []).length) {
      setDryChatId(String(cfg.source_peers![0]));
    } else {
      setDryChatId("");
    }
    setDryOpen(true);
  }

  function handleDryRun() {
    if (!dryRule) return;
    crud.dryRun({
      rid: dryRule.id,
      payload: {
        sample_message: drySample,
        // forward 不区分 chat type，固定 group 即可（后端只看 source_kind）
        sample_chat_type: "group",
        sample_chat_id: dryChatId ? Number(dryChatId) : undefined,
      },
      onSuccess: (res) => setDryResult(res),
    });
  }

  if (!aid) return <p>账号 ID 不合法</p>;

  return (
    <div className="space-y-6">
      <RulePageHeader
        title={`消息转发配置 · #${aid}`}
        backHref={`/accounts/${aid}?tab=features`}
      />

      <RuleInfoBox>
        <div>✅ 保存后立即生效，无需重启 worker。</div>
        <div>
          ⚠ <b>仅响应别人发来的消息</b>（incoming）。本账号自己发的消息不会被转发。
        </div>
        <div>
          🚦 每条转发都会过风控引擎；触发 FloodWait 会自动 sleep ≤60s
          后重试一次，最终失败仅写日志，不会让 worker 崩溃。
        </div>
      </RuleInfoBox>

      <RuleFeatureToggleCard
        enabled={crud.isFeatureEnabled}
        onToggle={crud.toggleFeature}
        description="关闭后所有转发规则都不会触发；启用即生效"
      />

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
                  <TableHead>源</TableHead>
                  <TableHead>目标</TableHead>
                  <TableHead>方式</TableHead>
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
                      <TableCell>{sourceLabel(cfg)}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {cfg.target_chat_id || (
                          <span className="text-muted-foreground">未设置</span>
                        )}
                      </TableCell>
                      <TableCell>{modeLabel(cfg.mode)}</TableCell>
                      <TableCell className="text-right">
                        <div className="inline-flex gap-1">
                          <Button size="sm" variant="ghost" onClick={() => openEdit(r)}>
                            <Pencil className="mr-1 h-3.5 w-3.5" /> 编辑
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => openDryRun(r)}>
                            <Play className="mr-1 h-3.5 w-3.5" /> 试运行
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
            <p className="py-8 text-center text-sm text-muted-foreground">
              暂无规则，点击右上角「新建规则」
            </p>
          )}
        </CardContent>
      </Card>

      {/* 编辑 / 新建 */}
      <RuleEditDialogShell
        open={editOpen}
        onOpenChange={setEditOpen}
        editing={editing}
        description={`"原生转发"显示原作者；"复制 / 引用"不显示；"仅链接"对公开超级群可点`}
        name={form.name}
        enabled={form.enabled}
        priority={form.priority}
        onNameChange={(v) => setForm({ ...form, name: v })}
        onEnabledChange={(v) => setForm({ ...form, enabled: v })}
        onPriorityChange={(v) => setForm({ ...form, priority: v })}
        onSave={handleSave}
        saving={crud.saving}
      >
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
            <option value="duplicate">复读检测（不同用户发相同文本）</option>
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

        {form.config.source_kind === "duplicate" && (
          <>
            <div className="rounded-md border px-3 py-2 text-xs alert-warning space-y-1">
              <div>
                <b>复读检测</b>：当同一群内 ≥N 个<b>不同用户</b>发送相同文本时触发转发（同一用户发多次只算 1 人）。
              </div>
              <div>同内容同群每天只触发一次（UTC+8 午夜重置），适用于复读、刷屏检测等场景。</div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="时间窗口（秒）">
                <Input
                  type="number"
                  min={1}
                  max={3600}
                  value={form.config.duplicate_window ?? 60}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        duplicate_window: Number(e.target.value) || 60,
                      },
                    })
                  }
                  placeholder="60"
                />
                <p className="text-xs text-muted-foreground">默认 60 秒</p>
              </Field>
              <Field label="不同用户数阈值">
                <Input
                  type="number"
                  min={2}
                  max={100}
                  value={form.config.duplicate_threshold ?? 3}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        duplicate_threshold: Number(e.target.value) || 3,
                      },
                    })
                  }
                  placeholder="3"
                />
                <p className="text-xs text-muted-foreground">
                  达到此人数时触发，同一用户多次只算 1 人
                </p>
              </Field>
            </div>
          </>
        )}

        <Field label="目标 chat_id（可选）">
          <Input
            inputMode="numeric"
            value={targetText}
            onChange={(e) => setTargetText(e.target.value.replace(/[^0-9-]/g, ""))}
            placeholder="留空 = 转发到消息来源的 chat；例：-1001234567890"
          />
        </Field>

        <Field label="转发方式">
          <Select
            value={form.config.mode}
            onChange={(e) =>
              setForm({
                ...form,
                config: { ...form.config, mode: e.target.value as ForwardMode },
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
              <span className="text-xs text-muted-foreground">关 = 仅纯文本通过</span>
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
      </RuleEditDialogShell>

      {/* 试运行 */}
      <DryRunDialogShell
        open={dryOpen}
        onOpenChange={setDryOpen}
        rule={dryRule}
        description="输入一条样例消息，验证 source_kind 是否命中（不会真的下发转发）"
        maxWidthClass="max-w-lg"
        onRun={handleDryRun}
        runDisabled={!drySample}
        pending={crud.dryRunPending}
        result={dryResult}
      >
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
            onChange={(e) => setDryChatId(e.target.value.replace(/[^0-9-]/g, ""))}
          />
        </Field>
      </DryRunDialogShell>
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
    case "duplicate":
      return `复读检测 (${cfg.duplicate_window ?? 60}s / ${cfg.duplicate_threshold ?? 3}人)`;
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
