import { ModelBrandLogo } from "@/components/ai/ModelBrandLogo";
import {
  formatStageTimings,
  resolveModelRunSummary,
  resolveToolCallCount,
  type ModelRunMetaUsage,
} from "@/lib/modelRunMeta";
import { cn } from "@/lib/utils";

function num(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

function str(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  return null;
}

function formatElapsed(ms: number | null): string | null {
  if (ms == null) return null;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function formatTokens(value: number | null): string {
  return value == null ? "–" : value.toLocaleString("zh-CN");
}

/**
 * 模型执行信息行（Agent 回答下方 / 测活结果复用）。
 * 不显示费用。
 */
export function ModelRunMeta({
  usage,
  expected,
  className,
  compact,
}: {
  usage?: ModelRunMetaUsage;
  expected?: { providerName?: string; model?: string } | null;
  className?: string;
  /** 单行紧凑模式（测活列表） */
  compact?: boolean;
}) {
  if (!usage || typeof usage !== "object") return null;

  const run = resolveModelRunSummary(usage);
  const provider = str(usage.provider_name);
  const model = str(usage.model);
  const input = num(usage.input_tokens);
  const output = num(usage.output_tokens);
  const tools = resolveToolCallCount(usage);
  const retries = num(usage.retry_count);
  const elapsed = num(usage.elapsed_ms);
  const apiFormat = str(usage.api_format);
  const usedFallback = run?.usedFallback ?? false;
  const streamFallback = Boolean(usage.stream_fallback);
  const selectionMode = str(usage.selection_mode);
  const reported = run?.reported ?? "";
  const requested = run?.requested ?? "";
  const summaryModel = run?.summaryModel ?? "";
  const summary = run?.summary ?? "";
  const expectedLabel = [expected?.providerName, expected?.model].filter(Boolean).join(" · ");
  const mismatch =
    Boolean(expectedLabel) && Boolean(reported) && expectedLabel !== reported;

  const stageBits = formatStageTimings(usage);
  const bits: string[] = [];
  if (summary) bits.push(summary);
  if (input != null || output != null) {
    bits.push(`in ${input ?? "–"} / out ${output ?? "–"}`);
  }
  if (tools != null) bits.push(`工具 ${tools}`);
  if (retries != null && retries > 0) bits.push(`重试 ${retries}`);
  const elapsedLabel = formatElapsed(elapsed);
  if (elapsedLabel) bits.push(`总耗时 ${elapsedLabel}`);
  if (apiFormat && compact) bits.push(apiFormat);

  if (!bits.length && !stageBits.length) return null;

  if (compact) {
    return (
      <div className={cn("text-[11px] leading-4 text-muted-foreground tabular-nums", className)}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          {summary ? (
            <span className="inline-flex min-w-0 items-center gap-1.5">
              <ModelBrandLogo model={summaryModel || model} providerName={provider} size={12} />
              <span className="min-w-0 break-all">{summary}</span>
            </span>
          ) : null}
          {bits.filter((bit) => bit !== summary).map((bit) => <span key={bit}>{bit}</span>)}
          {usedFallback ? <span className="rounded border border-warning/40 px-1 text-warning">fallback</span> : null}
          {streamFallback ? <span className="rounded border px-1">完整响应</span> : null}
          {selectionMode === "pinned" ? <span className="rounded border px-1">本轮固定</span> : null}
        </div>
        {stageBits.length ? (
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 opacity-90">
            {stageBits.map((bit) => <span key={bit}>{bit}</span>)}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "w-full max-w-full space-y-1.5 rounded-lg border border-border/60 bg-muted/20 px-2.5 py-2 text-[11px] leading-4 text-muted-foreground tabular-nums sm:w-auto sm:rounded-none sm:border-0 sm:bg-transparent sm:px-0 sm:py-0",
        className,
      )}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        {summary ? (
          <span className={cn("inline-flex min-w-0 items-center gap-1.5 font-medium text-foreground/80", usedFallback && mismatch && "text-warning")}>
            <ModelBrandLogo model={summaryModel || model} providerName={provider} size={13} />
            <span className="min-w-0 break-all">{summary}</span>
          </span>
        ) : null}
        {apiFormat ? <span className="rounded bg-muted px-1.5 py-0.5">{apiFormat}</span> : null}
        <span className="flex-1" />
        {usedFallback ? <span className="rounded border border-warning/40 px-1 text-warning">fallback</span> : null}
        {streamFallback ? <span className="rounded border px-1">完整响应</span> : null}
        {selectionMode === "pinned" ? <span className="rounded border px-1">本轮固定</span> : null}
      </div>
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        {input != null || output != null ? (
          <span className="inline-flex flex-wrap items-center gap-x-2">
            <span>输入 {formatTokens(input)}</span>
            <span>输出 {formatTokens(output)}</span>
          </span>
        ) : null}
        {tools != null ? <span>工具 {tools}</span> : null}
        {retries != null && retries > 0 ? <span>重试 {retries}</span> : null}
        {elapsedLabel ? <span>总耗时 {elapsedLabel}</span> : null}
      </div>
      {stageBits.length ? (
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-t border-border/50 pt-1.5 text-[10px] sm:border-0 sm:pt-0">
          {stageBits.map((bit) => (
            <span key={bit}>{bit}</span>
          ))}
        </div>
      ) : null}
      {usedFallback && requested && reported && requested !== reported ? (
        <div className="space-y-0.5 border-t border-border/50 pt-1.5 text-[10px] text-muted-foreground sm:border-0 sm:pt-0">
          <div>原模型：{requested}</div>
          <div>实际使用：{reported}</div>
          <div>原因：主模型不可用或已切换</div>
        </div>
      ) : null}
      {mismatch && !usedFallback ? (
        <div className="break-words border-t border-border/50 pt-1.5 text-[10px] text-muted-foreground sm:border-0 sm:pt-0">
          本轮请求 {expectedLabel}；上游返回模型标识 {reported}
        </div>
      ) : null}
    </div>
  );
}
