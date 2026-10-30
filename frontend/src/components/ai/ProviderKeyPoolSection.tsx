// Provider 密钥池：同一 Provider 下挂多把 API Key，按策略选钥，坏 Key 单独冷却。
//
// 三层选钥顺序（后端 llm_key_pool.py）：主密钥（保底） → 池内 enabled 的 Key。
// 策略只影响池内顺序：order 按优先级、rotate 轮转、least_used 当日用量最少优先。
// 出参永远不含明文，只有 label 与尾号 key_hint；编辑时重填 api_key 才会替换。
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { KeyRound, Pencil, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SecretInput } from "@/components/ui/secret-input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { MetaBadge } from "@/components/ui/meta-badge";
import { Spinner } from "@/components/ui/misc";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import {
  createLLMProviderKey,
  deleteLLMProviderKey,
  listLLMProviderKeys,
  patchLLMProviderKey,
} from "@/api/commands";
import type {
  LLMKeyStrategy,
  LLMProviderKeyHealth,
  LLMProviderKeyOut,
  LLMProviderKeyUpdate,
  ProxyOut,
} from "@/api/types";
import { getErrMsg } from "@/lib/api";
import { confirmDialog } from "@/components/ui/confirm-dialog";

const STRATEGY_OPTIONS: { value: LLMKeyStrategy; label: string; hint: string }[] = [
  { value: "order", label: "按优先级顺序", hint: "永远先用 priority 最小的那把；只有它不可用时才落到下一把" },
  { value: "rotate", label: "轮转", hint: "每次请求依次换一把，把并发摊到多把 Key 上" },
  { value: "least_used", label: "当日用量最少优先", hint: "按当日 Token 用量挑最少的那把，适合额度分散的多把免费 Key" },
];

type KeyForm = {
  label: string;
  api_key: string;
  enabled: boolean;
  priority: string;
  proxy_id: string;
  daily_request_limit: string;
  daily_token_limit: string;
  notes: string;
};

const EMPTY_KEY_FORM: KeyForm = {
  label: "",
  api_key: "",
  enabled: true,
  priority: "100",
  proxy_id: "",
  daily_request_limit: "",
  daily_token_limit: "",
  notes: "",
};

function formFromKey(key: LLMProviderKeyOut): KeyForm {
  return {
    label: key.label,
    api_key: "",
    enabled: key.enabled,
    priority: String(key.priority),
    proxy_id: key.proxy_id ? String(key.proxy_id) : "",
    daily_request_limit: key.daily_request_limit ? String(key.daily_request_limit) : "",
    daily_token_limit: key.daily_token_limit ? String(key.daily_token_limit) : "",
    notes: key.notes || "",
  };
}

function toIntOrNull(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : null;
}

function HealthBadge({ health }: { health: LLMProviderKeyHealth }) {
  const cooling = health.state === "cooling";
  const title = cooling
    ? [health.last_error_class, health.last_error_message].filter(Boolean).join(" · ")
    : "近期调用正常";
  return (
    <MetaBadge
      tone={cooling ? "danger" : "success"}
      className="text-[10px] leading-4"
      title={title || ""}
    >
      {cooling
        ? `冷却 ${health.cooldown_remaining_seconds}s`
        : health.consecutive_failures > 0
          ? `正常（连续失败 ${health.consecutive_failures}）`
          : "正常"}
    </MetaBadge>
  );
}

export function ProviderKeyPoolSection({
  providerId,
  strategy,
  onStrategyChange,
  proxyOptions,
  proxyById,
  disabled,
}: {
  providerId: number;
  strategy: LLMKeyStrategy;
  onStrategyChange: (next: LLMKeyStrategy) => void;
  proxyOptions: ProxyOut[];
  proxyById: Map<number, ProxyOut>;
  disabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingKey, setEditingKey] = useState<LLMProviderKeyOut | null>(null);
  const [form, setForm] = useState<KeyForm>(EMPTY_KEY_FORM);

  const keysQ = useQuery({
    queryKey: ["llm-provider-keys", providerId],
    queryFn: () => listLLMProviderKeys(providerId),
  });

  // 冷却剩余秒数是后端快照值，界面每秒本地递减，避免高频轮询。
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick((v) => v + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["llm-provider-keys", providerId] });
    queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
  };

  const saveMut = useMutation({
    mutationFn: async () => {
      const label = form.label.trim();
      if (!label) throw new Error("请填写密钥标签");
      const priority = toIntOrNull(form.priority) ?? 100;
      if (editingKey) {
        const payload: LLMProviderKeyUpdate = {
          label,
          enabled: form.enabled,
          priority,
          daily_request_limit: toIntOrNull(form.daily_request_limit),
          daily_token_limit: toIntOrNull(form.daily_token_limit),
          notes: form.notes.trim() || null,
        };
        if (form.api_key.trim()) payload.api_key = form.api_key.trim();
        if (form.proxy_id) payload.proxy_id = Number(form.proxy_id);
        else payload.clear_proxy = true;
        return patchLLMProviderKey(providerId, editingKey.id, payload);
      }
      if (!form.api_key.trim()) throw new Error("请填写 API Key");
      return createLLMProviderKey(providerId, {
        label,
        api_key: form.api_key.trim(),
        enabled: form.enabled,
        priority,
        proxy_id: form.proxy_id ? Number(form.proxy_id) : null,
        daily_request_limit: toIntOrNull(form.daily_request_limit),
        daily_token_limit: toIntOrNull(form.daily_token_limit),
        notes: form.notes.trim() || null,
      });
    },
    onSuccess: () => {
      toast.success(editingKey ? "密钥已更新" : "密钥已加入池");
      setDialogOpen(false);
      setEditingKey(null);
      setForm(EMPTY_KEY_FORM);
      invalidate();
    },
    onError: (error) => toast.error(getErrMsg(error)),
  });

  const toggleMut = useMutation({
    mutationFn: (vars: { keyId: number; enabled: boolean }) =>
      patchLLMProviderKey(providerId, vars.keyId, { enabled: vars.enabled }),
    onSuccess: () => invalidate(),
    onError: (error) => toast.error(getErrMsg(error)),
  });

  const deleteMut = useMutation({
    mutationFn: (keyId: number) => deleteLLMProviderKey(providerId, keyId),
    onSuccess: () => {
      toast.success("密钥已移除");
      invalidate();
    },
    onError: (error) => toast.error(getErrMsg(error)),
  });

  const openCreate = () => {
    setEditingKey(null);
    setForm(EMPTY_KEY_FORM);
    setDialogOpen(true);
  };

  const openEdit = (key: LLMProviderKeyOut) => {
    setEditingKey(key);
    setForm(formFromKey(key));
    setDialogOpen(true);
  };

  const proxyLabel = (proxyId: number | null | undefined) => {
    if (!proxyId) return "沿用 Provider 代理";
    const proxy = proxyById.get(proxyId);
    return proxy ? `${proxy.type}://${proxy.host}:${proxy.port}` : `#${proxyId}`;
  };

  const data = keysQ.data;
  const activeStrategy = STRATEGY_OPTIONS.find((s) => s.value === strategy) || STRATEGY_OPTIONS[0];

  return (
    <div className="rounded-md border bg-muted/30 p-3 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <Label className="text-sm font-semibold">密钥池（多 Key 轮换）</Label>
          <p className="text-xs text-muted-foreground">
            同一 Provider 可挂多把 Key：主密钥永远保底，池内 Key 按策略选中。
            遇到 401/429 这类只跟 Key 有关的错误会换一把重试，不消耗重试次数；
            坏 Key 自动冷却一段时间后恢复。
          </p>
        </div>
        <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={openCreate}>
          <Plus className="mr-1 h-4 w-4" />
          添加密钥
        </Button>
      </div>

      <div className="space-y-1">
        <Label className="text-xs">选钥策略</Label>
        <Select
          disabled={disabled}
          value={strategy}
          onChange={(event) => onStrategyChange(event.target.value as LLMKeyStrategy)}
        >
          {STRATEGY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <p className="text-xs text-muted-foreground">{activeStrategy.hint}</p>
      </div>

      {keysQ.isLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Spinner /> 正在读取密钥池…
        </div>
      ) : keysQ.isError ? (
        <p className="rounded-md border px-3 py-1.5 text-xs alert-warning">
          {getErrMsg(keysQ.error)}
        </p>
      ) : (
        <div className="space-y-2">
          <div className="rounded-md border bg-background overflow-hidden">
            <div className="flex items-center gap-2 border-b px-2 py-1.5 text-sm last:border-b-0">
              <KeyRound className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="flex-1 truncate">主密钥（Provider 自身 API Key）</span>
              {data?.has_main_key ? (
                data.main_key_health ? (
                  <HealthBadge health={data.main_key_health} />
                ) : (
                  <MetaBadge tone="success" className="text-[10px] leading-4">已配置</MetaBadge>
                )
              ) : (
                <MetaBadge tone="neutral" className="text-[10px] leading-4">未配置</MetaBadge>
              )}
            </div>
            {(data?.keys || []).map((key) => (
              <div
                key={key.id}
                className="flex items-center gap-2 border-b px-2 py-1.5 text-sm last:border-b-0"
              >
                <Switch
                  checked={key.enabled}
                  disabled={disabled || toggleMut.isPending}
                  onCheckedChange={(checked) => toggleMut.mutate({ keyId: key.id, enabled: checked })}
                />
                <span className="truncate" title={key.label}>
                  {key.label}
                </span>
                <span className="font-mono text-xs text-muted-foreground">
                  {key.key_hint ? `••••${key.key_hint}` : "••••"}
                </span>
                <MetaBadge tone="outline" className="text-[10px] leading-4" title="优先级，越小越先被选中">
                  P{key.priority}
                </MetaBadge>
                {key.proxy_id ? (
                  <MetaBadge tone="info" className="text-[10px] leading-4" title={proxyLabel(key.proxy_id)}>
                    独立代理
                  </MetaBadge>
                ) : null}
                {key.daily_request_limit ? (
                  <MetaBadge
                    tone={key.today_requests >= key.daily_request_limit ? "danger" : "neutral"}
                    className="text-[10px] leading-4"
                    title="当日请求数 / 每日上限"
                  >
                    {key.today_requests}/{key.daily_request_limit}
                  </MetaBadge>
                ) : (
                  <MetaBadge tone="neutral" className="text-[10px] leading-4" title="当日请求数">
                    {key.today_requests} 次
                  </MetaBadge>
                )}
                <div className="flex-1" />
                <HealthBadge health={key.health} />
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  title="编辑"
                  disabled={disabled}
                  onClick={() => openEdit(key)}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  title="移除"
                  disabled={disabled || deleteMut.isPending}
                  onClick={async () => {
                    if (!(await confirmDialog({ title: `移除密钥「${key.label}」？`, description: "移除后这把密钥不再参与轮换，需要时可以重新添加。", confirmLabel: "移除", tone: "danger" }))) return;
                    deleteMut.mutate(key.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-destructive" />
                </Button>
              </div>
            ))}
          </div>
          {data && data.keys.length === 0 ? (
            <p className="rounded-md border border-dashed py-3 text-center text-xs text-muted-foreground">
              尚未添加池内密钥，当前只使用主密钥。加第二把 Key 才能在限流或封号时自动切换。
            </p>
          ) : null}
        </div>
      )}

      <Dialog
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open);
          if (!open) {
            setEditingKey(null);
            setForm(EMPTY_KEY_FORM);
          }
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingKey ? `编辑密钥「${editingKey.label}」` : "添加池内密钥"}</DialogTitle>
            <DialogDescription>
              API Key 在服务端用主密钥加密落库，界面只显示尾号；留空表示不修改。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label className="text-xs">标签</Label>
              <Input
                value={form.label}
                maxLength={64}
                onChange={(event) => setForm({ ...form, label: event.target.value })}
                placeholder="例如：key-a / 主号 / 备用2"
              />
            </div>
            <SecretInput
              className="space-y-1"
              label={<span className="text-xs">密钥池 API Key{editingKey ? "（留空不改）" : ""}</span>}
              configured={Boolean(editingKey)}
              revealTarget={editingKey ? { kind: "llm_provider_key", id: editingKey.id } : null}
              value={form.api_key}
              onChange={(next) => setForm({ ...form, api_key: next })}
              placeholder="sk-..."
            />
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs">优先级（越小越先选中）</Label>
                <Input
                  value={form.priority}
                  inputMode="numeric"
                  onChange={(event) => setForm({ ...form, priority: event.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">出口代理</Label>
                <Select
                  value={form.proxy_id}
                  onChange={(event) => setForm({ ...form, proxy_id: event.target.value })}
                >
                  <option value="">沿用 Provider 代理</option>
                  {proxyOptions.map((proxy) => (
                    <option key={proxy.id} value={String(proxy.id)}>
                      {proxy.type}://{proxy.host}:{proxy.port}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs">每日请求上限（留空不限）</Label>
                <Input
                  value={form.daily_request_limit}
                  inputMode="numeric"
                  onChange={(event) => setForm({ ...form, daily_request_limit: event.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">每日 Token 上限（留空不限）</Label>
                <Input
                  value={form.daily_token_limit}
                  inputMode="numeric"
                  onChange={(event) => setForm({ ...form, daily_token_limit: event.target.value })}
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={form.enabled}
                onCheckedChange={(checked) => setForm({ ...form, enabled: checked })}
              />
              <span className="text-sm">参与选钥（关闭后只保留配置，不再被选中）</span>
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>
              取消
            </Button>
            <Button type="button" loading={saveMut.isPending} onClick={() => saveMut.mutate()}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
