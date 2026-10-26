/** Agent Skill「默认版本 vs 当前版本」unified diff 的纯函数解析（无运行时依赖，便于 node:test 直接测试）。 */

export type SkillDiffLineKind = "file" | "hunk" | "add" | "del" | "context" | "meta";

export interface SkillDiffLine {
  kind: SkillDiffLineKind;
  /** 去掉 +/-/空格 前缀后的正文；file/hunk/meta 行保留原文。 */
  text: string;
  /** 原始行（含前缀），用于复制或无障碍朗读。 */
  raw: string;
}

export interface SkillDiffSummary {
  lines: SkillDiffLine[];
  added: number;
  removed: number;
  hunks: number;
}

/** 把服务器返回的 unified diff 拆成带类型的行；空 diff 返回空列表。 */
export function parseSkillDiff(diff: string | null | undefined): SkillDiffSummary {
  const out: SkillDiffSummary = { lines: [], added: 0, removed: 0, hunks: 0 };
  if (!diff) return out;
  const rows = diff.replace(/\r\n?/g, "\n").split("\n");
  // 末尾换行产生的空串不算一行
  if (rows.length && rows[rows.length - 1] === "") rows.pop();
  for (const raw of rows) {
    if (raw.startsWith("+++ ") || raw.startsWith("--- ") || raw === "+++" || raw === "---") {
      out.lines.push({ kind: "file", text: raw, raw });
    } else if (raw.startsWith("@@")) {
      out.hunks += 1;
      out.lines.push({ kind: "hunk", text: raw, raw });
    } else if (raw.startsWith("+")) {
      out.added += 1;
      out.lines.push({ kind: "add", text: raw.slice(1), raw });
    } else if (raw.startsWith("-")) {
      out.removed += 1;
      out.lines.push({ kind: "del", text: raw.slice(1), raw });
    } else if (raw.startsWith("\\")) {
      out.lines.push({ kind: "meta", text: raw, raw });
    } else {
      out.lines.push({ kind: "context", text: raw.startsWith(" ") ? raw.slice(1) : raw, raw });
    }
  }
  return out;
}

/** 折叠未改动的上下文：只保留每处改动前后 `keep` 行，其余合并成一个省略标记。 */
export function collapseSkillDiffContext(
  lines: SkillDiffLine[],
  keep = 2,
): Array<SkillDiffLine | { kind: "gap"; count: number }> {
  const changed = lines.map((line) => line.kind === "add" || line.kind === "del");
  const result: Array<SkillDiffLine | { kind: "gap"; count: number }> = [];
  let gap = 0;
  const near = (index: number) => {
    for (let offset = -keep; offset <= keep; offset += 1) {
      if (changed[index + offset]) return true;
    }
    return false;
  };
  lines.forEach((line, index) => {
    if (line.kind !== "context" || near(index)) {
      if (gap) result.push({ kind: "gap", count: gap });
      gap = 0;
      result.push(line);
    } else {
      gap += 1;
    }
  });
  if (gap) result.push({ kind: "gap", count: gap });
  return result;
}
