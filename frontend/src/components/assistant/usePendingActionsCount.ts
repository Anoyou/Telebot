import { useQuery } from "@tanstack/react-query";

import { listSystemAgentActions } from "@/api/systemAgent";

/**
 * 「待确认」数：侧栏 Agent 行、桌面小宠物、手机底栏 Agent 圆钮共用同一个查询缓存。
 * AI 模块关闭时不请求，返回 0。
 */
export function usePendingActionsCount(enabled = true): number {
  const pendingActionsQ = useQuery({
    queryKey: ["system-agent", "actions", "pending-badge"],
    queryFn: () => listSystemAgentActions({ status: "pending", limit: 50 }),
    refetchInterval: 30_000,
    staleTime: 10_000,
    enabled,
  });
  return enabled ? pendingActionsQ.data?.length ?? 0 : 0;
}
