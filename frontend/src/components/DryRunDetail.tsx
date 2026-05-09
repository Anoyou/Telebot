import { Badge } from "@/components/ui/badge";

interface LogEntry {
  step: string;
  msg: string;
}

interface DryRunDetailProps {
  detail?: Record<string, unknown> | null;
}

const STEP_LABELS: Record<string, string> = {
  scope: "作用域",
  match: "匹配",
  source: "源筛选",
  mode: "转发方式",
  kind: "触发类型",
  now: "当前时间",
  once: "单次",
  interval: "间隔",
  cron: "Cron",
  action: "动作",
  result: "结果",
  state: "运行状态",
  error: "错误",
};

export function DryRunDetail({ detail }: DryRunDetailProps) {
  if (!detail) return null;

  const logs = detail.logs as LogEntry[] | undefined;
  if (!logs || logs.length === 0) {
    // 无 logs 时 fallback 到展示 detail 的 JSON
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50/60 p-3 text-xs space-y-1">
        <div className="font-medium text-amber-800">详细信息</div>
        <pre className="whitespace-pre-wrap text-amber-700">
          {JSON.stringify(detail, null, 2)}
        </pre>
      </div>
    );
  }

  return (
    <div className="rounded-md border p-3 text-xs space-y-2">
      <div className="font-medium">详细日志</div>
      <div className="space-y-1">
        {logs.map((entry, i) => {
          const label = STEP_LABELS[entry.step] || entry.step;
          const isResult = entry.step === "result";
          const isError = entry.step === "error";
          return (
            <div key={i} className="flex gap-2">
              <Badge
                variant={isError ? "destructive" : isResult ? "outline" : "secondary"}
                className="h-5 shrink-0 text-[10px] px-1.5"
              >
                {label}
              </Badge>
              <span className={isError ? "text-destructive" : ""}>{entry.msg}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
