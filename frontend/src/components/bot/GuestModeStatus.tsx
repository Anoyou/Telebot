import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, UserRoundSearch } from "lucide-react";

import {
  getAccountBotCapabilities,
  type AccountBotCapabilityStatus,
} from "@/api/accountBots";
import { Button } from "@/components/ui/button";
import { SignalPill, type VisualTone } from "@/components/ui/status";
import { getErrMsg } from "@/lib/api";

type GuestModeRole = "management" | "interaction";

const ROLE_COPY: Record<GuestModeRole, { label: string; usage: string }> = {
  management: {
    label: "管理 Bot",
    usage: "授权用户在任意聊天 @管理 Bot 即可执行斜杠命令；开启系统助手模式后，自由文本会交给系统助手回答。",
  },
  interaction: {
    label: "交互 Bot",
    usage: "任何人在任意聊天 @交互 Bot 都会进入交互规则（按规则会话范围）和订阅了 guest_message 的插件。",
  },
};

function guestModeTone(status?: AccountBotCapabilityStatus): VisualTone {
  if (!status?.configured) return "neutral";
  if (status.error) return "warn";
  if (status.supports_guest_queries) return "success";
  return "neutral";
}

function guestModeValue(status?: AccountBotCapabilityStatus): string {
  if (!status?.configured) return "未配置 Bot";
  if (status.error) return "无法读取";
  if (status.supports_guest_queries === true) return "已开启";
  if (status.supports_guest_queries === false) return "未开启";
  return "未知";
}

/** 访客模式（Guest Mode，Bot API 10.0）状态行：读取 getMe.supports_guest_queries。 */
export function GuestModeStatus({ aid, role }: { aid: number; role: GuestModeRole }) {
  const qc = useQueryClient();
  const queryKey = ["account", aid, "bot", "capabilities"];
  const capsQ = useQuery({
    queryKey,
    queryFn: () => getAccountBotCapabilities(aid),
    enabled: !!aid,
    staleTime: 60_000,
  });
  const status = role === "management" ? capsQ.data?.management_bot : capsQ.data?.interaction_bot;
  const copy = ROLE_COPY[role];
  const runtimeDisabled = capsQ.data && capsQ.data.guest_updates_enabled === false;
  const refresh = async () => {
    const next = await getAccountBotCapabilities(aid, { refresh: true });
    qc.setQueryData(queryKey, next);
  };
  return (
    <div className="nested-surface-item space-y-2 border border-border/60 px-3 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <UserRoundSearch className="h-4 w-4 shrink-0 text-muted-foreground" />
        <SignalPill
          tone={capsQ.isLoading ? "neutral" : guestModeTone(status)}
          label="访客模式（Guest Mode）"
          value={capsQ.isLoading ? "读取中…" : guestModeValue(status)}
        />
        {status?.username ? (
          <span className="truncate text-xs text-muted-foreground">@{status.username}</span>
        ) : null}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="ml-auto h-8 px-2 text-xs"
          disabled={capsQ.isFetching}
          onClick={() => void refresh().catch(() => undefined)}
        >
          <RefreshCw className="mr-1 h-3.5 w-3.5" />
          重新检测
        </Button>
      </div>
      {capsQ.isError ? (
        <p className="text-xs text-destructive">{getErrMsg(capsQ.error)}</p>
      ) : null}
      {status?.error ? <p className="break-words text-xs text-warning">{status.error}</p> : null}
      {runtimeDisabled ? (
        <p className="text-xs text-warning">服务器设置里的“访客消息（Guest Mode）”运行开关已关闭，当前不会处理访客消息。</p>
      ) : null}
      <p className="text-xs leading-5 text-muted-foreground">
        {status?.supports_guest_queries
          ? `${copy.label}可以在未加入的群聊或私聊里被 @提及（或被回复）时回复一次。${copy.usage}`
          : `开启方法：在 Telegram 打开 @BotFather 的 Mini App，进入这个 ${copy.label} 的设置并启用 “Guest Mode”，然后点“重新检测”。开启后：${copy.usage}`}
      </p>
    </div>
  );
}
