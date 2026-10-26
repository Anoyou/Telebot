import assert from "node:assert/strict";
import test from "node:test";

import { collapseSkillDiffContext, parseSkillDiff } from "./skillDiff.ts";

const sample = [
  "--- 默认版本（插件内置）",
  "+++ 当前版本（你的修改）",
  "@@ -16,6 +16,6 @@",
  " 1. 上下文一",
  " 2. 上下文二",
  " 3. 上下文三",
  "-## PT 站点搜索",
  "+## PT 站点种子搜索",
  " 4. 上下文四",
  " 5. 上下文五",
  " 6. 上下文六",
  "",
].join("\n");

test("unified diff 按行分类并统计增删", () => {
  const parsed = parseSkillDiff(sample);
  assert.equal(parsed.added, 1);
  assert.equal(parsed.removed, 1);
  assert.equal(parsed.hunks, 1);
  assert.deepEqual(parsed.lines.slice(0, 3).map((line) => line.kind), ["file", "file", "hunk"]);
  const del = parsed.lines.find((line) => line.kind === "del");
  assert.equal(del?.text, "## PT 站点搜索");
  assert.equal(del?.raw, "-## PT 站点搜索");
  // 末尾换行不产生空行
  assert.equal(parsed.lines.at(-1)?.text, "6. 上下文六");
});

test("文件头 --- / +++ 不计入增删", () => {
  const parsed = parseSkillDiff("--- a\n+++ b\n");
  assert.equal(parsed.added, 0);
  assert.equal(parsed.removed, 0);
});

test("空 diff 与 CRLF", () => {
  assert.equal(parseSkillDiff("").lines.length, 0);
  assert.equal(parseSkillDiff(undefined).lines.length, 0);
  const parsed = parseSkillDiff("@@ -1 +1 @@\r\n-旧\r\n+新\r\n");
  assert.deepEqual(parsed.lines.map((line) => line.text), ["@@ -1 +1 @@", "旧", "新"]);
});

test("折叠远离改动的上下文", () => {
  const parsed = parseSkillDiff(sample);
  const collapsed = collapseSkillDiffContext(parsed.lines, 1);
  const kinds = collapsed.map((row) => row.kind);
  assert.deepEqual(kinds, ["file", "file", "hunk", "gap", "context", "del", "add", "context", "gap"]);
  assert.deepEqual(collapsed.filter((row) => row.kind === "gap").map((row) => ("count" in row ? row.count : 0)), [2, 2]);
});

test("没有上下文可折叠时原样返回", () => {
  const parsed = parseSkillDiff("@@ -1 +1 @@\n-a\n+b");
  assert.equal(collapseSkillDiffContext(parsed.lines).length, parsed.lines.length);
});
