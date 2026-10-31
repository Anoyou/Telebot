import assert from "node:assert/strict";
import test from "node:test";

import {
  PAYMENT_SOURCE_EXPLANATION,
  paymentSourceConfirmText,
  paymentSourcePluginRoute,
  paymentSourceSwitchView,
  type PaymentSourceStatus,
} from "./paymentSource.ts";

const base: PaymentSourceStatus = {
  authoritative: false,
  effective: "legacy",
  label: "以旧解析为准（插件只做对照）",
  setting_key: "log_retention.payment_source_plugin_authoritative",
  sources: [{ plugin_key: "payment-source-cn-transfer", display_name: "中文转账通知解析", active: true }],
};

test("旧服务器（无接口）或非转账通知插件不显示开关区", () => {
  assert.equal(paymentSourceSwitchView(null, "payment-source-cn-transfer").visible, false);
  assert.equal(paymentSourceSwitchView(base, "lucky_redpack").visible, false);
});

test("关闭：以旧解析为准", () => {
  const view = paymentSourceSwitchView(base, "payment-source-cn-transfer");
  assert.equal(view.visible, true);
  assert.equal(view.checked, false);
  assert.equal(view.stateLabel, "以旧解析为准（插件只做对照）");
  assert.equal(view.warning, null);
  assert.deepEqual(view.explanation, PAYMENT_SOURCE_EXPLANATION);
});

test("打开且插件在工作：以插件为准", () => {
  const view = paymentSourceSwitchView({ ...base, authoritative: true, effective: "plugin" }, "payment-source-cn-transfer");
  assert.equal(view.checked, true);
  assert.equal(view.tone, "success");
  assert.equal(view.stateLabel, "以插件为准（生效中）");
});

test("打开但插件没工作：提示仍按旧解析并给原因", () => {
  const view = paymentSourceSwitchView(
    {
      ...base,
      authoritative: true,
      effective: "legacy",
      sources: [{ plugin_key: "payment-source-cn-transfer", display_name: "x", active: false, inactive_reason: "插件已停用（全局关闭），转账通知只用旧解析" }],
    },
    "payment-source-cn-transfer",
  );
  assert.equal(view.tone, "warning");
  assert.match(view.stateLabel, /仍按旧解析/);
  assert.match(view.warning ?? "", /已停用/);
});

test("确认文案与路由", () => {
  assert.equal(paymentSourceConfirmText(true).confirmLabel, "打开");
  assert.match(paymentSourceConfirmText(false).description, /旧解析/);
  assert.equal(paymentSourcePluginRoute("payment-source-cn-transfer"), "/plugins/manage?detail=payment-source-cn-transfer");
});
