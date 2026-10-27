import type { SystemAgentQueueItem, SystemAgentRun } from "@/api/systemAgent";

const RUN_STATUS_PRIORITY: Record<string, number> = {
  waiting_approval: 0,
  waiting_input: 1,
  running: 2,
  queued: 3,
  failed: 4,
  succeeded: 5,
  cancelled: 6,
};

const OPEN_RUN_STATUSES = new Set([
  "queued",
  "running",
  "waiting_input",
  "waiting_approval",
]);

export type SystemAgentRunSettlement =
  | "waiting"
  | "complete"
  | "failed"
  | "cancelled";

export function classifySystemAgentRunSettlement(
  status: string,
): SystemAgentRunSettlement {
  if (status === "waiting_input" || status === "waiting_approval") return "waiting";
  if (status === "succeeded") return "complete";
  if (status === "cancelled") return "cancelled";
  return "failed";
}

function timestamp(value?: string | null): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

export function sortSystemAgentRuns(runs: SystemAgentRun[]): SystemAgentRun[] {
  return [...runs].sort((left, right) => {
    const statusDelta =
      (RUN_STATUS_PRIORITY[left.status] ?? 99) -
      (RUN_STATUS_PRIORITY[right.status] ?? 99);
    if (statusDelta !== 0) return statusDelta;
    const timeDelta =
      timestamp(right.updated_at || right.created_at) -
      timestamp(left.updated_at || left.created_at);
    return timeDelta || left.id.localeCompare(right.id);
  });
}

export function taskCenterVisibleRuns(
  runs: SystemAgentRun[],
  dismissedRunIds: ReadonlySet<string> = new Set(),
): SystemAgentRun[] {
  const latestByMessage = new Map<string, SystemAgentRun>();
  for (const run of runs) {
    if (run.user_message_id == null) continue;
    const key = `${run.session_id}:${run.user_message_id}`;
    const current = latestByMessage.get(key);
    if (
      !current
      || timestamp(run.updated_at || run.created_at)
        > timestamp(current.updated_at || current.created_at)
    ) {
      latestByMessage.set(key, run);
    }
  }

  return sortSystemAgentRuns(
    runs.filter((run) => {
      if (OPEN_RUN_STATUSES.has(run.status)) return true;
      if (run.status !== "failed" || dismissedRunIds.has(run.id)) return false;
      if (run.user_message_id == null) return true;
      return latestByMessage.get(`${run.session_id}:${run.user_message_id}`)?.id === run.id;
    }),
  ).slice(0, 20);
}

export function sessionRunStatusById(
  runs: SystemAgentRun[],
): Record<string, string> {
  const statuses: Record<string, string> = {};

  // 未结束的运行需要持续提示；同一会话出现多个未结束记录时沿用任务中心优先级。
  for (const run of sortSystemAgentRuns(runs)) {
    if (
      statuses[run.session_id] === undefined
      && OPEN_RUN_STATUSES.has(run.status)
    ) {
      statuses[run.session_id] = run.status;
    }
  }

  // 已结束的运行只看该会话最新一次结果，避免历史失败覆盖后续成功。
  const latestBySession = new Map<string, SystemAgentRun>();
  for (const run of runs) {
    const current = latestBySession.get(run.session_id);
    if (
      current === undefined
      || timestamp(run.updated_at || run.created_at)
        > timestamp(current.updated_at || current.created_at)
    ) {
      latestBySession.set(run.session_id, run);
    }
  }
  for (const [sessionId, run] of latestBySession) {
    if (statuses[sessionId] === undefined && run.status === "failed") {
      statuses[sessionId] = run.status;
    }
  }

  return statuses;
}

export function sortSystemAgentQueue(
  queue: SystemAgentQueueItem[],
): SystemAgentQueueItem[] {
  return [...queue].sort((left, right) => {
    const positionDelta = left.position - right.position;
    if (positionDelta !== 0) return positionDelta;
    const timeDelta = timestamp(left.created_at) - timestamp(right.created_at);
    return timeDelta || left.id.localeCompare(right.id);
  });
}

/** 任务的真实阶段（服务器 phase 字段）；排队中且前面有任务时显示“前面 N 个”。没有数据时退回任务类型。 */
const PHASE_LABELS: Record<string, string> = {
  starting: "正在开始",
  recovering: "正在恢复",
  understanding: "理解问题",
  checking_model: "连接模型",
  selecting_model: "连接模型",
  routing: "选择工具",
  selecting_skill: "加载技能",
  thinking: "思考中",
  using_tool: "调用工具",
  responding: "生成回复",
  finalizing: "整理回复",
  finishing: "整理回复",
  steered: "已应用调整",
  paused: "已暂停",
};

export function runPhaseLabel(run: { status: string; phase?: string; kind: string; queue_ahead?: number | null }): string {
  if (run.status === "queued") {
    return run.queue_ahead && run.queue_ahead > 0 ? `排队中 · 前面 ${run.queue_ahead} 个` : "正在开始";
  }
  if (run.status === "waiting_input") return "等待补充信息";
  if (run.status === "waiting_approval") return "等待审批";
  if (run.status !== "running") return run.kind;
  return (run.phase && PHASE_LABELS[run.phase]) || run.phase || run.kind;
}
