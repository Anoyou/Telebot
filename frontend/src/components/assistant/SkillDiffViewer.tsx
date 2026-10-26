import { Fragment, useEffect, useMemo, useState } from "react";
import { ChevronsUpDown, Columns2, Rows3 } from "lucide-react";
import {
  buildSplitRows,
  buildUnifiedRows,
  diffSkillText,
  type InlineSegment,
  type SideCell,
  type SplitRow,
  type UnifiedRow,
} from "@/lib/skillDiff";
import { cn } from "@/lib/utils";

type View = "split" | "unified";
const SPLIT_QUERY = "(min-width: 768px)";

function useWideEnough(): boolean {
  const [wide, setWide] = useState(() => typeof window !== "undefined" && window.matchMedia?.(SPLIT_QUERY).matches);
  useEffect(() => {
    const media = window.matchMedia?.(SPLIT_QUERY);
    if (!media) return;
    const onChange = () => setWide(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return !!wide;
}

// 行底色 / 行号底色 / 改动字词底色：亮暗主题各自调过对比度。
const TONE = {
  del: {
    line: "bg-rose-500/[0.09] dark:bg-rose-500/[0.14]",
    gutter: "bg-rose-500/[0.16] text-rose-800/80 dark:bg-rose-500/[0.22] dark:text-rose-200/80",
    word: "rounded-[3px] bg-rose-500/30 text-rose-950 dark:bg-rose-400/40 dark:text-white",
    mark: "text-rose-700 dark:text-rose-300",
  },
  add: {
    line: "bg-emerald-500/[0.09] dark:bg-emerald-500/[0.14]",
    gutter: "bg-emerald-500/[0.16] text-emerald-800/80 dark:bg-emerald-500/[0.22] dark:text-emerald-200/80",
    word: "rounded-[3px] bg-emerald-500/30 text-emerald-950 dark:bg-emerald-400/40 dark:text-white",
    mark: "text-emerald-700 dark:text-emerald-300",
  },
  equal: { line: "", gutter: "text-muted-foreground/70", word: "", mark: "text-transparent" },
} as const;

const CODE = "min-w-0 whitespace-pre-wrap break-words px-2 [overflow-wrap:anywhere]";
const GUTTER = "select-none px-2 text-right tabular-nums";

function Code({ text, segments, kind }: { text: string; segments?: InlineSegment[]; kind: "equal" | "del" | "add" }) {
  if (!text && !segments?.length) return <>{"\u00a0"}</>;
  if (!segments) return <>{text}</>;
  return (
    <>
      {segments.map((segment, index) =>
        segment.changed ? (
          <mark key={index} data-diff-word={kind} className={cn("px-px", TONE[kind].word)}>
            {segment.text}
          </mark>
        ) : (
          <Fragment key={index}>{segment.text}</Fragment>
        ),
      )}
    </>
  );
}

function GapRow({ count, onExpand, span }: { count: number; onExpand: () => void; span: string }) {
  return (
    <button
      type="button"
      data-diff-gap
      onClick={onExpand}
      className={cn(
        span,
        "flex items-center gap-2 border-y border-sky-500/20 bg-sky-500/[0.07] px-3 py-1 text-left font-sans text-[11px] text-sky-800 hover:bg-sky-500/[0.14] dark:text-sky-200",
      )}
    >
      <ChevronsUpDown className="h-3.5 w-3.5" aria-hidden />
      展开 {count} 行未改动内容
    </button>
  );
}

function SplitCell({ cell, side }: { cell: SideCell | null; side: "left" | "right" }) {
  if (!cell) {
    return (
      <>
        <div aria-hidden className={cn(GUTTER, "bg-muted/50")} />
        <div aria-hidden className="bg-[repeating-linear-gradient(135deg,transparent,transparent_5px,hsl(var(--muted))_5px,hsl(var(--muted))_6px)] opacity-70" />
      </>
    );
  }
  const tone = TONE[cell.kind];
  return (
    <>
      <div className={cn(GUTTER, tone.gutter, side === "right" && "border-l border-border/60")}>{cell.no}</div>
      <div data-diff-kind={cell.kind} className={cn(CODE, tone.line)}>
        {cell.kind !== "equal" ? <span className="sr-only">{cell.kind === "add" ? "新增：" : "删除："}</span> : null}
        <Code text={cell.text} segments={cell.segments} kind={cell.kind} />
      </div>
    </>
  );
}

function renderSplit(rows: SplitRow[], expanded: Set<number>, expand: (id: number) => void): JSX.Element[] {
  return rows.flatMap((row): JSX.Element[] => {
    if (row.type === "gap") {
      if (expanded.has(row.id)) return renderSplit(row.rows, expanded, expand);
      return [<GapRow key={`g${row.id}`} count={row.count} span="col-span-4" onExpand={() => expand(row.id)} />];
    }
    return [
      <Fragment key={`s-${row.left?.kind ?? "x"}${row.left?.no ?? ""}-${row.right?.kind ?? "x"}${row.right?.no ?? ""}`}>
        <SplitCell cell={row.left} side="left" />
        <SplitCell cell={row.right} side="right" />
      </Fragment>,
    ];
  });
}

function renderUnified(rows: UnifiedRow[], expanded: Set<number>, expand: (id: number) => void): JSX.Element[] {
  return rows.flatMap((row): JSX.Element[] => {
    if (row.type === "gap") {
      if (expanded.has(row.id)) return renderUnified(row.rows, expanded, expand);
      return [<GapRow key={`g${row.id}`} count={row.count} span="col-span-3" onExpand={() => expand(row.id)} />];
    }
    const tone = TONE[row.kind];
    return [
      <Fragment key={`u-${row.kind}-${row.oldNo ?? ""}-${row.newNo ?? ""}`}>
        <div className={cn(GUTTER, tone.gutter)}>{row.oldNo ?? ""}</div>
        <div className={cn(GUTTER, tone.gutter)}>{row.newNo ?? ""}</div>
        <div data-diff-kind={row.kind} className={cn("flex min-w-0", tone.line)}>
          <span aria-hidden className={cn("w-4 shrink-0 select-none text-center", tone.mark)}>
            {row.kind === "add" ? "+" : row.kind === "del" ? "−" : ""}
          </span>
          {row.kind !== "equal" ? <span className="sr-only">{row.kind === "add" ? "新增：" : "删除："}</span> : null}
          <span className={cn(CODE, "flex-1 pl-0")}>
            <Code text={row.text} segments={row.segments} kind={row.kind} />
          </span>
        </div>
      </Fragment>,
    ];
  });
}

/**
 * 代码对比视图（类似 GitHub / VS Code）：等宽字体、两侧行号、删除红底 / 新增绿底、
 * 行内改动字词深色高亮、未改动大段折叠为「展开 N 行」；宽屏双栏，窄屏自动统一视图，长行自动换行。
 */
export function SkillDiffViewer({
  oldText,
  newText,
  oldLabel = "默认版本（插件内置）",
  newLabel = "当前版本（你的修改）",
}: {
  oldText: string;
  newText: string;
  oldLabel?: string;
  newLabel?: string;
}) {
  const wide = useWideEnough();
  const [chosen, setChosen] = useState<View | null>(null);
  const view: View = chosen ?? (wide ? "split" : "unified");
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set());
  const diff = useMemo(() => diffSkillText(oldText, newText), [oldText, newText]);
  const splitRows = useMemo(() => buildSplitRows(diff), [diff]);
  const unifiedRows = useMemo(() => buildUnifiedRows(diff), [diff]);
  const gapIds = useMemo(() => (view === "split" ? splitRows : unifiedRows).filter((row) => row.type === "gap").map((row) => (row as { id: number }).id), [view, splitRows, unifiedRows]);
  const allExpanded = gapIds.every((id) => expanded.has(id));
  const expand = (id: number) => setExpanded((prev) => new Set(prev).add(id));
  useEffect(() => setExpanded(new Set()), [diff, view]);

  if (diff.added + diff.removed === 0) {
    return (
      <p data-testid="agent-skill-diff" className="rounded-md border bg-muted/30 px-3 py-6 text-center text-xs text-muted-foreground">
        {newLabel}与{oldLabel}一致。
      </p>
    );
  }

  return (
    <div className="min-w-0 space-y-2" data-testid="agent-skill-diff" data-diff-view={view}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
        <span className="rounded-md bg-emerald-500/15 px-1.5 py-0.5 font-mono font-semibold text-emerald-700 dark:text-emerald-300" aria-label={`新增 ${diff.added} 行`}>
          +{diff.added}
        </span>
        <span className="rounded-md bg-rose-500/15 px-1.5 py-0.5 font-mono font-semibold text-rose-700 dark:text-rose-300" aria-label={`删除 ${diff.removed} 行`}>
          −{diff.removed}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {gapIds.length ? (
            <button
              type="button"
              className="inline-flex min-h-8 items-center gap-1 rounded-md px-2 text-muted-foreground hover:bg-muted hover:text-foreground"
              onClick={() => setExpanded(allExpanded ? new Set() : new Set(gapIds))}
            >
              <ChevronsUpDown className="h-3.5 w-3.5" aria-hidden />
              {allExpanded ? "折叠未改动" : "全部展开"}
            </button>
          ) : null}
          <div role="radiogroup" aria-label="对比视图" className="inline-flex rounded-md border border-border/70 bg-muted/40 p-0.5">
            {([
              ["split", "双栏", Columns2],
              ["unified", "统一", Rows3],
            ] as const).map(([value, label, Icon]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={view === value}
                onClick={() => setChosen(value)}
                className={cn(
                  "inline-flex min-h-7 items-center gap-1 rounded px-2 font-medium",
                  view === value ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden />
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="overflow-hidden rounded-lg border border-border/80 bg-card font-mono text-[12px] leading-5">
        {view === "split" ? (
          <div className="grid grid-cols-[auto_minmax(0,1fr)_auto_minmax(0,1fr)]">
            <div className="col-span-2 border-b border-border/70 bg-muted/60 px-3 py-1.5 font-sans text-[11px] font-medium text-rose-700 dark:text-rose-300">− {oldLabel}</div>
            <div className="col-span-2 border-b border-l border-border/70 bg-muted/60 px-3 py-1.5 font-sans text-[11px] font-medium text-emerald-700 dark:text-emerald-300">+ {newLabel}</div>
            {renderSplit(splitRows, expanded, expand)}
          </div>
        ) : (
          <div className="grid grid-cols-[auto_auto_minmax(0,1fr)]">
            <div className="col-span-3 border-b border-border/70 bg-muted/60 px-3 py-1.5 font-sans text-[11px] font-medium text-muted-foreground">
              <span className="text-rose-700 dark:text-rose-300">− {oldLabel}</span>
              <span className="mx-2">→</span>
              <span className="text-emerald-700 dark:text-emerald-300">+ {newLabel}</span>
            </div>
            {renderUnified(unifiedRows, expanded, expand)}
          </div>
        )}
      </div>
      {diff.degraded ? <p className="text-xs text-muted-foreground">内容较长，改动区按整段显示，未逐行对齐。</p> : null}
    </div>
  );
}
