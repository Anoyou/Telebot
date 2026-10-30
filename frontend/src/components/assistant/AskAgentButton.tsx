import { Sparkles } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { withAgentAccountSearch, type AgentAccountSource } from "./assistantDockState";

/**
 * 「问 Agent」：带当前账号打开 Agent（第 20 轮）。
 * 在当前地址加 ?agent_account=ID&agent_from=…，Dock 展开并进入带该账号的新会话草稿，参数随即被消费。
 */
export function AskAgentButton({
  accountId,
  source,
  className,
}: {
  accountId: number | null | undefined;
  source: AgentAccountSource;
  className?: string;
}) {
  const nav = useNavigate();
  const location = useLocation();
  if (!accountId) return null;
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      data-testid="ask-agent-with-account"
      className={cn("shrink-0", className)}
      onClick={() =>
        nav(
          {
            pathname: location.pathname,
            search: withAgentAccountSearch(location.search, { id: accountId, source }),
            hash: location.hash,
          },
          { replace: true },
        )
      }
    >
      <Sparkles className="mr-1 h-4 w-4" />
      问 Agent
    </Button>
  );
}
