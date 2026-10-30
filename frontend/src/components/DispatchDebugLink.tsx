// 「命中调试」入口链接：日志页、交互页里指向 /dispatch-debug（第 20 轮，信息架构第一批）。
// 命中调试模块被关闭时不显示，与侧栏隐藏规则一致；能力查询与侧栏共用缓存。
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bug } from "lucide-react";

import { getPlatformCapabilities, getSystemSettings } from "@/api/system";
import { Button } from "@/components/ui/button";
import {
  capabilityEnabledMap,
  dispatchDebugHref,
  type DispatchDebugPrefill,
} from "@/lib/navigation";

export function useDispatchDebugEnabled(): boolean {
  const settingsQ = useQuery({ queryKey: ["system", "settings"], queryFn: getSystemSettings, staleTime: 30_000 });
  const capsQ = useQuery({ queryKey: ["system", "capabilities"], queryFn: getPlatformCapabilities, staleTime: 15_000 });
  if (capsQ.isLoading) return false;
  return capabilityEnabledMap(capsQ.data, settingsQ.data?.ai_enabled ?? true).dispatch_debug !== false;
}

export function DispatchDebugLink({
  prefill,
  label = "命中调试",
  title = "想知道一条消息为什么没回复？用命中调试模拟它会被谁处理",
  variant = "outline",
}: {
  prefill?: DispatchDebugPrefill;
  label?: string;
  title?: string;
  variant?: "outline" | "ghost" | "secondary";
}) {
  const enabled = useDispatchDebugEnabled();
  if (!enabled) return null;
  return (
    <Button asChild variant={variant} size="sm" title={title}>
      <Link to={dispatchDebugHref(prefill)} data-dispatch-debug-link>
        <Bug className="mr-1.5 h-4 w-4" />
        {label}
      </Link>
    </Button>
  );
}
