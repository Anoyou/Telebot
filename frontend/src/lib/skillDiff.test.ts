import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSplitRows,
  buildUnifiedRows,
  collapseSkillDiffContext,
  diffInline,
  diffSkillText,
  parseSkillDiff,
  tokenizeForDiff,
} from "./skillDiff.ts";

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


const OLD = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "旧行：PT 站点搜索", "k", "l"].join("\n");
const NEW = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "新行：PT 站点种子搜索", "插入", "k", "l"].join("\n");

test("行级对比：统计、行号与顺序", () => {
  const diff = diffSkillText(OLD, NEW);
  assert.equal(diff.added, 2);
  assert.equal(diff.removed, 1);
  assert.equal(diff.degraded, false);
  const del = diff.ops.find((op) => op.type === "del");
  assert.deepEqual(del, { type: "del", text: "旧行：PT 站点搜索", oldNo: 11 });
  const adds = diff.ops.filter((op) => op.type === "add");
  assert.deepEqual(adds.map((op) => op.newNo), [11, 12]);
  // 删除排在新增前
  assert.ok(diff.ops.indexOf(del!) < diff.ops.indexOf(adds[0]));
  assert.equal(diff.ops.at(-1)?.oldNo, 13);
  assert.equal(diff.ops.at(-1)?.newNo, 14);
});

test("相同文本没有改动", () => {
  const diff = diffSkillText("x\ny\n", "x\ny");
  assert.equal(diff.added + diff.removed, 0);
});

test("中文逐字、英文按词分词", () => {
  assert.deepEqual(tokenizeForDiff("PT 站点"), ["PT", " ", "站", "点"]);
});

test("字词级高亮只标出改动部分", () => {
  const { left, right } = diffInline("旧行：PT 站点搜索", "新行：PT 站点种子搜索");
  assert.deepEqual(left.filter((s) => s.changed).map((s) => s.text), ["旧"]);
  assert.deepEqual(right.filter((s) => s.changed).map((s) => s.text), ["新", "种子"]);
  assert.equal(left.map((s) => s.text).join(""), "旧行：PT 站点搜索");
});

test("差异过大时整行标记", () => {
  const { left } = diffInline("完全不同的一句话", "something else entirely");
  assert.deepEqual(left, [{ text: "完全不同的一句话", changed: true }]);
});

test("双栏：配对修改行、折叠远处上下文并保留原行", () => {
  const rows = buildSplitRows(diffSkillText(OLD, NEW), 3);
  const gap = rows[0];
  assert.equal(gap.type, "gap");
  assert.equal(gap.type === "gap" && gap.count, 7);
  assert.equal(gap.type === "gap" && gap.rows.length, 7);
  const changed = rows.filter((row) => row.type === "line" && (row.left?.kind !== "equal" || row.right?.kind !== "equal"));
  assert.equal(changed.length, 2);
  const first = changed[0];
  assert.ok(first.type === "line" && first.left?.segments && first.right?.segments);
  const second = changed[1];
  assert.ok(second.type === "line" && second.left === null && second.right?.text === "插入");
});

test("统一视图：删除在前、新增在后，各带行号", () => {
  const rows = buildUnifiedRows(diffSkillText(OLD, NEW), 3).filter((row) => row.type === "line" && row.kind !== "equal");
  assert.deepEqual(rows.map((row) => row.type === "line" && [row.kind, row.oldNo, row.newNo]), [
    ["del", 11, undefined],
    ["add", undefined, 11],
    ["add", undefined, 12],
  ]);
});
