import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Scale } from "lucide-react";
import { toast } from "sonner";

import { getPaymentSourceStatus, patchSystemSettings, type SystemSettingsPatch } from "@/api/system";
import { MetaBadge } from "@/components/ui/meta-badge";
import { Switch } from "@/components/ui/switch";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { getErrMsg } from "@/lib/api";
import {
  PAYMENT_SOURCE_SETTINGS_ROUTE,
  paymentSourceConfirmText,
  paymentSourceSwitchView,
} from "@/lib/paymentSource";

export const PAYMENT_SOURCE_STATUS_QK = ["payment-sources", "status"] as const;

const TONE_CLASS = {
  success: "border-success/30 bg-success/10 text-success",
  warning: "border-warning/30 bg-warning/10 text-warning",
  neutral: "",
} as const;

/**
 * 第 22 轮：转账通知插件详情里的「转账通知判定」开关区。
 * 只在服务器有 GET /api/payment-sources/status、且这个插件是转账通知插件时显示；
 * 开关就是服务器参数 log_retention.payment_source_plugin_authoritative，状态以服务器当下配置为准。
 */
export function PaymentSourceSwitchSection({ pluginKey }: { pluginKey: string }) {
  const qc = useQueryClient();
  const statusQ = useQuery({ queryKey: PAYMENT_SOURCE_STATUS_QK, queryFn: getPaymentSourceStatus, retry: false });
  const view = paymentSourceSwitchView(statusQ.data, pluginKey);
  const mutation = useMutation({
    mutationFn: (next: boolean) => patchSystemSettings({ log_retention: { payment_source_plugin_authoritative: next } } as unknown as SystemSettingsPatch),
    onSuccess: (_data, next) => {
      toast.success(next ? "已改为以插件结果为准，30 秒内生效" : "已改回以旧解析为准，30 秒内生效");
      qc.invalidateQueries({ queryKey: PAYMENT_SOURCE_STATUS_QK });
      qc.invalidateQueries({ queryKey: ["system", "settings"] });
      qc.invalidateQueries({ queryKey: ["plugin-ui-card"] });
    },
    onError: (error) => toast.error(`保存失败：${getErrMsg(error)}（请刷新确认当前状态）`),
  });
  if (!view.visible) return null;
  const toggle = async (next: boolean) => {
    const text = paymentSourceConfirmText(next);
    if (!(await confirmDialog(text))) return;
    mutation.mutate(next);
  };
  return (
    <section className="rounded-md border border-border/70 bg-background/80 p-4" data-testid="payment-source-switch">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
            <Scale className="h-4 w-4 text-muted-foreground" aria-hidden />
            转账通知以插件结果为准
            <MetaBadge tone="outline" className={TONE_CLASS[view.tone]} data-testid="payment-source-switch-state">
              {view.stateLabel}
            </MetaBadge>
          </div>
          <code className="mt-1 block break-all text-[11px] text-muted-foreground">payment_source_plugin_authoritative</code>
        </div>
        <Switch
          checked={view.checked}
          disabled={mutation.isPending}
          aria-label="转账通知以插件结果为准"
          data-testid="payment-source-switch-toggle"
          onCheckedChange={(next) => void toggle(next)}
        />
      </div>
      <ul className="mt-3 space-y-1 text-xs leading-5 text-muted-foreground">
        {view.explanation.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {view.warning ? (
        <p className="mt-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning" data-testid="payment-source-switch-warning">
          {view.warning}
        </p>
      ) : null}
      <Link
        to={PAYMENT_SOURCE_SETTINGS_ROUTE}
        className="mt-3 inline-flex items-center gap-0.5 text-xs text-primary hover:underline"
        data-testid="payment-source-switch-settings-link"
      >
        在「服务器设置 › 平台与模块」里也能改
        <ArrowUpRight className="h-3 w-3" aria-hidden />
      </Link>
    </section>
  );
}
