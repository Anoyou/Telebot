import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/misc";
import type { LLMProviderOut } from "@/api/types";

export interface RoutingFieldsValue {
  routing_mode: "fixed" | "auto";
  fallback_provider_id: string;
  classifier_provider_id: string;
}

export function RoutingFields({
  value,
  providers,
  loading,
  onChange,
}: {
  value: RoutingFieldsValue;
  providers?: LLMProviderOut[];
  loading: boolean;
  onChange: (patch: Partial<RoutingFieldsValue>) => void;
}) {
  return (
    <div className="space-y-3">
      <div>
        <Label className="text-sm font-semibold">路由模式</Label>
        <p className="text-xs text-muted-foreground">
          fixed = 固定使用上面选的模型；auto = 看消息类型自动路由调用模型（详见 AI
          配置示例）
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label
          className={
            "cursor-pointer rounded-md border p-3 text-sm transition-colors " +
            (value.routing_mode === "fixed"
              ? "border-primary bg-primary/5"
              : "hover:bg-muted")
          }
        >
          <input
            type="radio"
            name="routingMode"
            className="mr-2"
            checked={value.routing_mode === "fixed"}
            onChange={() => onChange({ routing_mode: "fixed" })}
          />
          <span className="font-medium">fixed（固定）</span>
          <p className="mt-1 text-xs text-muted-foreground">
            简单可控；适合"我就要某个模型"
          </p>
        </label>
        <label
          className={
            "cursor-pointer rounded-md border p-3 text-sm transition-colors " +
            (value.routing_mode === "auto"
              ? "border-primary bg-primary/5"
              : "hover:bg-muted")
          }
        >
          <input
            type="radio"
            name="routingMode"
            className="mr-2"
            checked={value.routing_mode === "auto"}
            onChange={() => onChange({ routing_mode: "auto" })}
          />
          <span className="font-medium">auto（自动路由）</span>
          <p className="mt-1 text-xs text-muted-foreground">
            按消息类型选合适的模型；省钱 + 更对路
          </p>
        </label>
      </div>

      {value.routing_mode === "auto" && (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>独立兜底模型提供商（可选）</Label>
            <ProviderSelect
              value={value.fallback_provider_id}
              providers={providers}
              loading={loading}
              onChange={(v) => onChange({ fallback_provider_id: v })}
              allowEmpty
            />
            <p className="text-xs text-muted-foreground">
              留空 = 直接复用上面那条「默认 / 兜底模型提供商」；想分开就在这选另一条
            </p>
          </div>
          <div className="space-y-1.5">
            <Label>分类器模型提供商（可选）</Label>
            <ProviderSelect
              value={value.classifier_provider_id}
              providers={providers}
              loading={loading}
              onChange={(v) => onChange({ classifier_provider_id: v })}
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
        尚未配置模型提供商。先到「AI → 模型提供商」新建一个
      </div>
    );
  }
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{allowEmpty ? "— 不指定 —" : "— 请选择 —"}</option>
      {providers.map((p) => (
        <option key={p.id} value={String(p.id)}>
          {p.name}（{p.provider} · {p.default_model}）
          {p.has_effective_key ? "" : " · ⚠ 未配置 key"}
          {p.tags && p.tags.length > 0 ? ` · [${p.tags.join(",")}]` : ""}
        </option>
      ))}
    </Select>
  );
}
