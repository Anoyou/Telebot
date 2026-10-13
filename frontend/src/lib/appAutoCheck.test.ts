import assert from "node:assert/strict";
import test from "node:test";

import {
  resolveCustomIntervalMinutes,
  shouldShowUpdateBadge,
  updateBadgeLabel,
  isAutoCheckPreset,
  minutesToHours,
} from "./appAutoCheck.ts";

test("间隔分钟转小时不会带浮点尾巴", () => {
  assert.equal(minutesToHours(1440), 24);
  assert.equal(minutesToHours(360), 6);
  assert.equal(minutesToHours(4500), 75);
  assert.equal(minutesToHours(90), 1.5);
});

test("预设命中判定", () => {
  assert.equal(isAutoCheckPreset(1440), true);
  assert.equal(isAutoCheckPreset(10080), true);
  assert.equal(isAutoCheckPreset(1000), false);
  assert.equal(isAutoCheckPreset(undefined), false);
});

test("自定义间隔：合法输入换算成分钟", () => {
  assert.deepEqual(resolveCustomIntervalMinutes(" 6 "), { ok: true, minutes: 360 });
  assert.deepEqual(resolveCustomIntervalMinutes("1.5"), { ok: true, minutes: 90 });
});

test("自定义间隔：越界与非法输入给可读错误", () => {
  const tooSmall = resolveCustomIntervalMinutes("0.5");
  assert.equal(tooSmall.ok, false);
  assert.match((tooSmall as { error: string }).error, /1 小时到 168 小时/);

  const tooBig = resolveCustomIntervalMinutes("999");
  assert.equal(tooBig.ok, false);

  for (const bad of ["", "   ", "abc", "-3", "0"]) {
    const result = resolveCustomIntervalMinutes(bad);
    assert.equal(result.ok, false, `输入 ${JSON.stringify(bad)} 必须被拒绝`);
  }
});

test("角标只在确认有新版本且环境可检查时出现", () => {
  assert.equal(
    shouldShowUpdateBadge({ last_check: { has_update: true, can_check: true } }),
    true,
  );
  // 已是最新
  assert.equal(
    shouldShowUpdateBadge({ last_check: { has_update: false, can_check: true } }),
    false,
  );
  // 容器内无法自动检查：has_update 不是真实结论，不误导用户
  assert.equal(
    shouldShowUpdateBadge({ last_check: { has_update: true, can_check: false } }),
    false,
  );
  // 还没跑过一次自动检查 / 查询失败
  assert.equal(shouldShowUpdateBadge({ last_check: null }), false);
  assert.equal(shouldShowUpdateBadge(null), false);
  assert.equal(shouldShowUpdateBadge(undefined), false);
});

test("有更新时顶栏文案说明原因，方便读屏与 tooltip", () => {
  assert.equal(updateBadgeLabel(true), "检查更新（发现新版本）");
  assert.equal(updateBadgeLabel(false), "检查更新");
});
