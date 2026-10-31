// 第 22 轮：「转账通知以插件结果为准」开关（log_retention.payment_source_plugin_authoritative）的展示规则。
// 生效与否完全以服务器当下配置为准；这里只把 GET /api/payment-sources/status 整理成界面文案。
// 与 iOS PaymentSourceSwitchModel 同一套文案。

export const PAYMENT_SOURCE_SETTING_KEY = "log_retention.payment_source_plugin_authoritative";
export const PAYMENT_SOURCE_SETTINGS_ROUTE = "/settings?tab=platform";

export interface PaymentSourcePluginStatus {
  plugin_key: string;
  display_name: string;
  version?: string;
  source_ids?: string[];
  active: boolean;
  inactive_reason?: string | null;
}

export interface PaymentSourceStatus {
  authoritative: boolean;
  effective: "plugin" | "legacy";
  label: string;
  setting_key: string;
  sources?: PaymentSourcePluginStatus[];
}

export type PaymentSourceTone = "success" | "neutral" | "warning";

export interface PaymentSourceSwitchView {
  /** 这个插件是不是转账通知插件（不是就不显示开关区）。 */
  visible: boolean;
  checked: boolean;
  stateLabel: string;
  tone: PaymentSourceTone;
  /** 插件此刻没参与解析时的原因（打开开关也不会生效）。 */
  warning: string | null;
  explanation: string[];
}

export const PAYMENT_SOURCE_EXPLANATION = [
  "关：以系统自带的旧解析为准，这个插件只在旁边对照，结果不用于结算。",
  "开：以这个插件读出的付款人、收款人和金额为准；旧格式认不出的可信 Bot 消息也交给插件判断。",
  "插件出错或超时会自动回退旧解析；随时可以关掉，30 秒内生效，结算规则本身不变。",
];

export function paymentSourceSwitchView(status: PaymentSourceStatus | null | undefined, pluginKey: string): PaymentSourceSwitchView {
  const source = status?.sources?.find((item) => item.plugin_key === pluginKey);
  if (!status || !source) {
    return { visible: false, checked: false, stateLabel: "", tone: "neutral", warning: null, explanation: [] };
  }
  const checked = Boolean(status.authoritative);
  let stateLabel: string;
  let tone: PaymentSourceTone;
  if (!checked) {
    stateLabel = "以旧解析为准（插件只做对照）";
    tone = "neutral";
  } else if (status.effective === "plugin" && source.active) {
    stateLabel = "以插件为准（生效中）";
    tone = "success";
  } else {
    stateLabel = "已打开，但这个插件没在工作，仍按旧解析";
    tone = "warning";
  }
  const warning = source.active ? null : source.inactive_reason || "这个插件此刻没有参与转账通知解析";
  return { visible: true, checked, stateLabel, tone, warning, explanation: PAYMENT_SOURCE_EXPLANATION };
}

export function paymentSourceConfirmText(next: boolean): { title: string; description: string; confirmLabel: string } {
  return next
    ? {
        title: "改为以插件结果为准？",
        description:
          "打开后转账通知以转账通知插件读出的结果为准（30 秒内生效）。插件出错或超时会自动回退旧解析，随时可以关掉。",
        confirmLabel: "打开",
      }
    : {
        title: "改回以旧解析为准？",
        description: "关闭后转账通知以系统自带的旧解析为准，插件只做对照（30 秒内生效）。",
        confirmLabel: "关闭",
      };
}

/** 插件详情的 Web 路由（服务器参数页、设置页跳过来用）。 */
export function paymentSourcePluginRoute(pluginKey: string): string {
  return `/plugins/manage?detail=${encodeURIComponent(pluginKey)}`;
}
