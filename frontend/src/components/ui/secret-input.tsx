// 统一密钥 / 密码 / Token 输入框（第 20 轮）。
//
// - 有标题（label），默认圆点遮罩，右侧「眼睛」切换明文；
// - 用户正在输入的新值：点眼睛直接在本地切换明文；
// - 服务器上已保存的旧值（configured=true 且输入框为空）：点眼睛时才调用
//   GET /api/secrets/reveal 取回明文（服务器写审计、no-store），只保存在组件内存里，
//   隐藏或离开页面即丢弃；没有 reveal 目标时显示「已配置 ••••••••」且眼睛只对新输入生效。
import * as React from "react";
import { Eye, EyeOff, Loader2 } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, getErrMsg } from "@/lib/api";
import { cn } from "@/lib/utils";

export const SECRET_CONFIGURED_MASK = "••••••••";

export type SecretKind =
  | "interaction_bot_token"
  | "transfer_bot_token"
  | "management_bot_token"
  | "notify_bot_token"
  | "llm_provider_api_key"
  | "llm_provider_key"
  | "proxy_password"
  | "webhook_token"
  | "plugin_repo_credential"
  | "plugin_config_secret";

export interface SecretRevealTarget {
  kind: SecretKind;
  account_id?: number | null;
  id?: number | null;
  plugin_key?: string | null;
  field?: string | null;
}

export interface SecretRevealResult {
  kind: string;
  label: string;
  configured: boolean;
  value: string | null;
}

/** 按需取回一项已保存密钥的明文；每次调用服务器都会写审计。 */
export async function revealSecret(target: SecretRevealTarget): Promise<SecretRevealResult> {
  const params: Record<string, string | number> = { kind: target.kind };
  if (target.account_id != null) params.account_id = target.account_id;
  if (target.id != null) params.id = target.id;
  if (target.plugin_key) params.plugin_key = target.plugin_key;
  if (target.field) params.field = target.field;
  const { data } = await api.get<SecretRevealResult>("/api/secrets/reveal", {
    params,
    headers: { "Cache-Control": "no-store" },
  });
  return data;
}

export interface SecretInputProps {
  /** 标题，例如「交互 Bot Token」。必须写清这是什么。 */
  label: React.ReactNode;
  /** 标题下方的一行说明（可选）。 */
  description?: React.ReactNode;
  /** 用户正在输入的新值；留空表示保持服务器上的旧值。 */
  value: string;
  onChange: (value: string) => void;
  /** 服务器上是否已保存这一项。 */
  configured?: boolean;
  /** 已保存值的查看目标；不传则眼睛只对新输入生效。 */
  revealTarget?: SecretRevealTarget | null;
  /** 未配置时的占位提示。 */
  placeholder?: string;
  /** 已配置时的占位提示，默认「已配置 ••••••••（留空保持不变）」。 */
  configuredPlaceholder?: string;
  id?: string;
  disabled?: boolean;
  autoComplete?: string;
  className?: string;
  inputClassName?: string;
  /** 输入框右侧额外按钮（例如「清空」）。 */
  trailing?: React.ReactNode;
  /** 标题右侧额外内容（例如状态徽标）。 */
  labelAside?: React.ReactNode;
  /** 紧凑行内布局：标题只给读屏，占位文字必须写明是什么（含已配置时）。 */
  hideLabel?: boolean;
  onKeyDown?: React.KeyboardEventHandler<HTMLInputElement>;
  "data-testid"?: string;
}

export function SecretInput({
  label,
  description,
  value,
  onChange,
  configured = false,
  revealTarget = null,
  placeholder,
  configuredPlaceholder,
  id,
  disabled,
  autoComplete = "off",
  className,
  inputClassName,
  trailing,
  labelAside,
  hideLabel = false,
  onKeyDown,
  "data-testid": testId,
}: SecretInputProps) {
  const autoId = React.useId();
  const inputId = id ?? `secret-${autoId}`;
  const [visible, setVisible] = React.useState(false);
  const [revealed, setRevealed] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const revealKey = revealTarget
    ? [revealTarget.kind, revealTarget.account_id, revealTarget.id, revealTarget.plugin_key, revealTarget.field].join("|")
    : "";

  // 目标变了（换账号 / 换供应商）或服务器侧状态变了：丢掉已取回的明文。
  React.useEffect(() => {
    setRevealed(null);
    setVisible(false);
    setError(null);
  }, [revealKey, configured]);

  const hasDraft = value.length > 0;
  const showingSaved = !hasDraft && configured;
  const canRevealSaved = showingSaved && Boolean(revealTarget);

  const toggle = async () => {
    setError(null);
    if (visible) {
      setVisible(false);
      setRevealed(null);
      return;
    }
    if (canRevealSaved && revealed === null && revealTarget) {
      setLoading(true);
      try {
        const result = await revealSecret(revealTarget);
        setRevealed(result.configured ? result.value ?? "" : "");
        if (!result.configured) setError("服务器上没有保存这一项");
      } catch (e) {
        setError(`读取失败：${getErrMsg(e)}`);
        setLoading(false);
        return;
      }
      setLoading(false);
    }
    setVisible(true);
  };

  const displayValue = hasDraft ? value : visible && revealed ? revealed : "";
  const eyeDisabled = disabled || loading || (!hasDraft && !canRevealSaved);
  const eyeLabel = visible ? "隐藏" : "显示";
  const resolvedPlaceholder = showingSaved
    ? configuredPlaceholder ?? `已配置 ${SECRET_CONFIGURED_MASK}（留空保持不变）`
    : placeholder;

  return (
    <div className={cn(hideLabel ? "min-w-0" : "space-y-1.5", className)} data-secret-input={revealTarget?.kind ?? "local"}>
      <div className={cn("flex items-center justify-between gap-2", hideLabel && "sr-only")}>
        <Label htmlFor={inputId}>{label}</Label>
        {labelAside}
      </div>
      {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Input
            id={inputId}
            data-testid={testId}
            type={visible ? "text" : "password"}
            autoComplete={autoComplete}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            disabled={disabled}
            placeholder={resolvedPlaceholder}
            value={displayValue}
            onKeyDown={onKeyDown}
            onChange={(e) => {
              // 在已取回的旧值上编辑 = 输入新值
              setRevealed(null);
              onChange(e.target.value);
            }}
            className={cn("pr-10 font-mono", inputClassName)}
          />
          <button
            type="button"
            onClick={() => void toggle()}
            disabled={eyeDisabled}
            aria-label={eyeLabel}
            aria-pressed={visible}
            title={
              !hasDraft && showingSaved && !revealTarget
                ? "已保存的值不能在这里查看；输入新值后可切换明文"
                : eyeLabel
            }
            data-secret-eye=""
            className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : visible ? (
              <EyeOff className="h-4 w-4" />
            ) : (
              <Eye className="h-4 w-4" />
            )}
          </button>
        </div>
        {trailing}
      </div>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
