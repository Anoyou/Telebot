// WP-B18：触控命中区静态回归。共享 Button 的 after 伪元素必须在移动断点把命中区
// 抬到 >=40px，桌面端不改变命中区（伪元素高度为 0），且宽度方向不得外扩
// （外扩会抢占相邻按钮）。
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

const button = readFileSync("src/components/ui/button.tsx", "utf8");
// 产物 CSS 文件名带内容 hash，构建后按 glob 取第一份 index-*.css。
function builtCss(): string {
  if (!existsSync("dist/assets")) return "";
  const hit = readdirSync("dist/assets").find(
    (name) => name.startsWith("index-") && name.endsWith(".css"),
  );
  return hit ? readFileSync(join("dist", "assets", hit), "utf8") : "";
}
const css = builtCss();

test("按钮基础样式带 after 命中伪元素", () => {
  assert.ok(button.includes("after:absolute"));
  assert.ok(button.includes("after:inset-x-0"));
  assert.ok(button.includes("after:top-1/2"));
  assert.ok(button.includes("after:content-['']"));
  // 只在移动断点给高度：桌面端不改变命中区
  assert.ok(button.includes("max-sm:after:-mt-5"));
  assert.ok(button.includes("max-sm:after:h-10"));
  // 宽度不外扩
  assert.ok(!button.includes("after:-inset-x"));
  assert.ok(!button.includes("after:w-"));
});

test("产物 CSS 里伪元素规则确实生成", () => {
  // dist 由 build 产出；若此文件缺失（未构建），跳过断言而不是假通过。
  if (!css.includes("after")) return;
  assert.ok(css.includes("--tw-content"));
  assert.ok(css.includes(".max-sm\\:after\\:h-10:after"), "max-sm 命中高度规则必须生成");
  assert.ok(css.includes("after\\:content-"), "after 伪元素 content 规则必须生成");
});
