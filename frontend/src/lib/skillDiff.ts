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

// ───────────────────────── 客户端代码对比（双栏 / 统一视图、行号、字词级高亮） ─────────────────────────

export type LineOp = { type: "equal" | "del" | "add"; text: string; oldNo?: number; newNo?: number };
export type InlineSegment = { text: string; changed: boolean };
export type SideCell = { no: number; text: string; kind: "equal" | "del" | "add"; segments?: InlineSegment[] };
export type SplitRow =
  | { type: "line"; left: SideCell | null; right: SideCell | null }
  | { type: "gap"; id: number; count: number; rows: SplitRow[] };
export type UnifiedRow =
  | { type: "line"; kind: "equal" | "del" | "add"; oldNo?: number; newNo?: number; text: string; segments?: InlineSegment[] }
  | { type: "gap"; id: number; count: number; rows: UnifiedRow[] };

export interface CodeDiff {
  ops: LineOp[];
  added: number;
  removed: number;
  /** 超出 LCS 规模上限时退化为整段替换（仍正确，只是不对齐）。 */
  degraded: boolean;
}

const MAX_LCS_CELLS = 4_000_000;

function splitLines(text: string): string[] {
  const normalized = text.replace(/\r\n?/g, "\n");
  if (normalized === "") return [];
  const rows = normalized.split("\n");
  if (rows[rows.length - 1] === "") rows.pop();
  return rows;
}

/** 通用 LCS 对齐：返回 a、b 中相等元素的下标对（升序）。 */
function lcsPairs<T>(a: T[], b: T[], eq: (x: T, y: T) => boolean): Array<[number, number]> | null {
  const n = a.length;
  const m = b.length;
  if (n === 0 || m === 0) return [];
  if (n * m > MAX_LCS_CELLS) return null;
  const width = m + 1;
  const table = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      table[i * width + j] = eq(a[i], b[j])
        ? table[(i + 1) * width + j + 1] + 1
        : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
  }
  const pairs: Array<[number, number]> = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (eq(a[i], b[j])) {
      pairs.push([i, j]);
      i += 1;
      j += 1;
    } else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) {
      i += 1;
    } else {
      j += 1;
    }
  }
  return pairs;
}

/** 行级对比：先裁掉公共前后缀再做 LCS；删除行排在同位置新增行之前。 */
export function diffSkillText(oldText: string, newText: string): CodeDiff {
  const a = splitLines(oldText);
  const b = splitLines(newText);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1;
    endB -= 1;
  }
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const pairs = lcsPairs(midA, midB, (x, y) => x === y);
  const degraded = pairs == null;
  const ops: LineOp[] = [];
  let oldNo = 1;
  let newNo = 1;
  const equal = (text: string) => ops.push({ type: "equal", text, oldNo: oldNo++, newNo: newNo++ });
  const del = (text: string) => ops.push({ type: "del", text, oldNo: oldNo++ });
  const add = (text: string) => ops.push({ type: "add", text, newNo: newNo++ });
  for (let k = 0; k < start; k += 1) equal(a[k]);
  let i = 0;
  let j = 0;
  for (const [pi, pj] of pairs ?? []) {
    while (i < pi) del(midA[i++]);
    while (j < pj) add(midB[j++]);
    equal(midA[i]);
    i += 1;
    j += 1;
  }
  while (i < midA.length) del(midA[i++]);
  while (j < midB.length) add(midB[j++]);
  for (let k = endA; k < a.length; k += 1) equal(a[k]);
  return {
    ops,
    added: ops.filter((op) => op.type === "add").length,
    removed: ops.filter((op) => op.type === "del").length,
    degraded,
  };
}

/** 分词：连续空白、ASCII 单词、单个 CJK/其它字符、单个标点各成一段，便于中文逐字对比。 */
export function tokenizeForDiff(text: string): string[] {
  return text.match(/\s+|[A-Za-z0-9_]+|[\s\S]/gu) ?? [];
}

const MAX_INLINE_TOKENS = 1500;

function mergeSegments(tokens: string[], changed: boolean[]): InlineSegment[] {
  const out: InlineSegment[] = [];
  tokens.forEach((token, index) => {
    const last = out[out.length - 1];
    if (last && last.changed === changed[index]) last.text += token;
    else out.push({ text: token, changed: changed[index] });
  });
  return out;
}

/** 一对修改行的字词级对比；改动过多（相似度很低）时整行标记，避免满屏碎片。 */
export function diffInline(oldLine: string, newLine: string): { left: InlineSegment[]; right: InlineSegment[] } {
  const a = tokenizeForDiff(oldLine);
  const b = tokenizeForDiff(newLine);
  const whole = { left: [{ text: oldLine, changed: true }], right: [{ text: newLine, changed: true }] };
  if (a.length > MAX_INLINE_TOKENS || b.length > MAX_INLINE_TOKENS) return whole;
  const pairs = lcsPairs(a, b, (x, y) => x === y) ?? [];
  const keptChars = pairs.reduce((sum, [i]) => sum + (a[i].trim() ? a[i].length : 0), 0);
  const total = Math.max(oldLine.replace(/\s/g, "").length, newLine.replace(/\s/g, "").length, 1);
  if (keptChars / total < 0.3) return whole;
  const changedA = a.map(() => true);
  const changedB = b.map(() => true);
  for (const [i, j] of pairs) {
    changedA[i] = false;
    changedB[j] = false;
  }
  return { left: mergeSegments(a, changedA), right: mergeSegments(b, changedB) };
}

type Block = { kind: "equal"; ops: LineOp[] } | { kind: "change"; dels: LineOp[]; adds: LineOp[] };

function toBlocks(ops: LineOp[]): Block[] {
  const blocks: Block[] = [];
  for (const op of ops) {
    const last = blocks[blocks.length - 1];
    if (op.type === "equal") {
      if (last?.kind === "equal") last.ops.push(op);
      else blocks.push({ kind: "equal", ops: [op] });
    } else {
      const block = last?.kind === "change" ? last : (blocks.push({ kind: "change", dels: [], adds: [] }), blocks[blocks.length - 1] as Extract<Block, { kind: "change" }>);
      if (op.type === "del") block.dels.push(op);
      else block.adds.push(op);
    }
  }
  return blocks;
}

/** 把未改动的长段（距改动超过 context 行）折叠成 gap，gap 内保留原始行以便“展开 N 行”。 */
function foldEqual<R>(
  equalOps: LineOp[],
  isFirst: boolean,
  isLast: boolean,
  context: number,
  render: (op: LineOp) => R,
  gap: (rows: R[]) => R,
): R[] {
  const head = isFirst ? 0 : context;
  const tail = isLast ? 0 : context;
  if (equalOps.length <= head + tail + 1) return equalOps.map(render);
  const rows = equalOps.map(render);
  return [...rows.slice(0, head), gap(rows.slice(head, rows.length - tail)), ...rows.slice(rows.length - tail)];
}

export function buildSplitRows(diff: CodeDiff, context = 3): SplitRow[] {
  const blocks = toBlocks(diff.ops);
  let gapId = 0;
  const out: SplitRow[] = [];
  blocks.forEach((block, index) => {
    if (block.kind === "equal") {
      out.push(
        ...foldEqual<SplitRow>(
          block.ops,
          index === 0,
          index === blocks.length - 1,
          context,
          (op) => ({
            type: "line",
            left: { no: op.oldNo!, text: op.text, kind: "equal" },
            right: { no: op.newNo!, text: op.text, kind: "equal" },
          }),
          (rows) => ({ type: "gap", id: gapId++, count: rows.length, rows }),
        ),
      );
      return;
    }
    const pairs = Math.max(block.dels.length, block.adds.length);
    for (let k = 0; k < pairs; k += 1) {
      const del = block.dels[k];
      const add = block.adds[k];
      const inline = del && add ? diffInline(del.text, add.text) : null;
      out.push({
        type: "line",
        left: del ? { no: del.oldNo!, text: del.text, kind: "del", segments: inline?.left } : null,
        right: add ? { no: add.newNo!, text: add.text, kind: "add", segments: inline?.right } : null,
      });
    }
  });
  return out;
}

export function buildUnifiedRows(diff: CodeDiff, context = 3): UnifiedRow[] {
  const blocks = toBlocks(diff.ops);
  let gapId = 0;
  const out: UnifiedRow[] = [];
  blocks.forEach((block, index) => {
    if (block.kind === "equal") {
      out.push(
        ...foldEqual<UnifiedRow>(
          block.ops,
          index === 0,
          index === blocks.length - 1,
          context,
          (op) => ({ type: "line", kind: "equal", oldNo: op.oldNo, newNo: op.newNo, text: op.text }),
          (rows) => ({ type: "gap", id: gapId++, count: rows.length, rows }),
        ),
      );
      return;
    }
    const inlines = block.dels.map((del, k) => (block.adds[k] ? diffInline(del.text, block.adds[k].text) : null));
    block.dels.forEach((del, k) => out.push({ type: "line", kind: "del", oldNo: del.oldNo, text: del.text, segments: inlines[k]?.left }));
    block.adds.forEach((add, k) => out.push({ type: "line", kind: "add", newNo: add.newNo, text: add.text, segments: inlines[k]?.right }));
  });
  return out;
}
