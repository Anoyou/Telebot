import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import {
  listPluginUICards,
  readPluginUICard,
  runPluginUICardAction,
  type PluginUICard,
  type PluginUICardSlot,
} from "@/api/plugins";
import { Button } from "@/components/ui/button";
import { MetaBadge } from "@/components/ui/meta-badge";
import { Spinner } from "@/components/ui/misc";
import { SignalPill, type VisualTone } from "@/components/ui/status";
import { getErrMsg } from "@/lib/api";
import {
  buildCardBlock,
  CARD_FAILURE_MESSAGE,
  CARD_RETRY_LABEL,
  type CardBlock,
  type CardBlockModel,
  type CardTone,
} from "@/lib/pluginCards";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/components/ui/confirm-dialog";

// 插件原生卡片（ui.cards）：Web 与 iOS 按同一份契约原生渲染，不为单个插件写页面。
// 数据逐卡读取，失败只影响那一张卡（「已停止工作 · 重试」）。

const PILL_TONE: Record<CardTone, VisualTone> = {
  neutral: "neutral",
  success: "success",
  warning: "warn",
  danger: "danger",
  accent: "primary",
};

const BADGE_TONE: Record<CardTone, "neutral" | "success" | "warn" | "danger" | "info"> = {
  neutral: "neutral",
  success: "success",
  warning: "warn",
  danger: "danger",
  accent: "info",
};

const TEXT_TONE: Record<CardTone, string> = {
  neutral: "",
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
  accent: "text-primary",
};

export function PluginCardSlot({
  slot,
  pluginKey,
  accountId,
  title,
  className,
}: {
  slot: PluginUICardSlot;
  pluginKey?: string;
  accountId?: number | null;
  /** 有卡片时显示的分组标题（dashboard / account.detail 用）。 */
  title?: string;
  className?: string;
}) {
  const cardsQ = useQuery({
    queryKey: ["plugin-ui-cards", slot, pluginKey ?? null, accountId ?? null],
    queryFn: () => listPluginUICards({ slot, pluginKey, accountId }),
    retry: false,
    staleTime: 60_000,
  });
  const cards = cardsQ.data?.cards ?? [];
  if (cardsQ.isLoading) {
    return pluginKey ? (
      <div className="flex h-16 items-center justify-center rounded-md border border-border/70"><Spinner className="text-primary" /></div>
    ) : null;
  }
  if (cardsQ.isError) {
    return pluginKey ? (
      <div className="rounded-md border border-border/70 p-4 text-sm text-destructive">插件卡片读取失败：{getErrMsg(cardsQ.error)}</div>
    ) : null;
  }
  if (cards.length === 0) return null;
  return (
    <div className={cn("space-y-3", className)} data-plugin-card-slot={slot}>
      {title ? <div className="text-sm font-medium text-muted-foreground">{title}</div> : null}
      {cards.map((card) => (
        <PluginCardView key={`${card.plugin_key}:${card.card_id}`} card={card} accountId={accountId} showPluginName={!pluginKey} />
      ))}
    </div>
  );
}

export function PluginCardView({
  card,
  accountId,
  showPluginName = false,
}: {
  card: PluginUICard;
  accountId?: number | null;
  showPluginName?: boolean;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["plugin-ui-card", card.plugin_key, card.card_id, accountId ?? null];
  const dataQ = useQuery({
    queryKey,
    queryFn: () => readPluginUICard(card, accountId),
    retry: false,
    staleTime: 15_000,
    refetchInterval: card.refresh_seconds ? card.refresh_seconds * 1000 : false,
  });
  const actionMut = useMutation({
    mutationFn: (actionId: string) => runPluginUICardAction(card, actionId, accountId),
    onSuccess: async (result) => {
      if (result.message) toast.success(result.message);
      if (result.refresh) await queryClient.invalidateQueries({ queryKey });
    },
    onError: (error) => toast.error(`操作失败：${getErrMsg(error)}`),
  });
  const failed = dataQ.isError || dataQ.data?.status === "error";
  const failureDetail = dataQ.isError ? getErrMsg(dataQ.error) : dataQ.data?.message;
  const models = !failed && dataQ.data
    ? card.blocks.map((block) => buildCardBlock(block as CardBlock, dataQ.data?.data ?? {}))
    : [];

  return (
    <section
      className="rounded-md border border-border/70 bg-background/80 p-4"
      data-plugin-card={card.card_id}
      data-plugin-card-plugin={card.plugin_key}
    >
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-medium">{card.title}</div>
          {card.subtitle || showPluginName ? (
            <div className="text-xs text-muted-foreground">
              {showPluginName ? card.plugin_name : null}
              {showPluginName && card.subtitle ? " · " : null}
              {card.subtitle}
            </div>
          ) : null}
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 px-2 text-xs"
          disabled={dataQ.isFetching}
          onClick={() => void dataQ.refetch()}
        >
          <RefreshCw className={cn("mr-1 h-3.5 w-3.5", dataQ.isFetching && "animate-spin")} />
          刷新
        </Button>
      </div>

      {dataQ.isLoading ? (
        <div className="flex h-16 items-center justify-center"><Spinner className="text-primary" /></div>
      ) : failed ? (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm" data-plugin-card-failed>
          <AlertTriangle className="h-4 w-4 text-destructive" />
          <span className="font-medium text-destructive">{CARD_FAILURE_MESSAGE}</span>
          <span className="text-muted-foreground">·</span>
          <button type="button" className="font-medium text-primary underline-offset-2 hover:underline" onClick={() => void dataQ.refetch()}>
            {CARD_RETRY_LABEL}
          </button>
          {failureDetail ? <span className="w-full break-all text-xs text-muted-foreground">{failureDetail}</span> : null}
        </div>
      ) : (
        <div className="space-y-3">
          {models.map((model, index) => (
            <CardBlockView
              key={index}
              model={model}
              blockId={String((card.blocks[index] as CardBlock)?.id ?? index)}
              busyAction={actionMut.isPending ? actionMut.variables : undefined}
              onAction={async (actionId, confirmText) => {
                if (confirmText && !(await confirmDialog({ title: "确认执行这个操作？", description: confirmText, confirmLabel: "执行" }))) return;
                actionMut.mutate(actionId);
              }}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function CardBlockView({
  model,
  blockId,
  busyAction,
  onAction,
}: {
  model: CardBlockModel;
  blockId: string;
  busyAction?: string;
  onAction: (actionId: string, confirm: string) => void;
}) {
  if (model.type === "stats") {
    return (
      <div className="flex flex-wrap gap-2" data-plugin-card-block="stats">
        {model.items.map((item, index) => (
          <SignalPill key={`${item.label}-${index}`} tone={PILL_TONE[item.tone]} label={item.label} value={item.text} />
        ))}
      </div>
    );
  }
  if (model.type === "kv") {
    return (
      <dl className="grid gap-x-4 gap-y-1.5 text-sm sm:grid-cols-[max-content_minmax(0,1fr)]" data-plugin-card-block="kv">
        {model.items.map((item, index) => (
          <div key={`${item.label}-${index}`} className="contents">
            <dt className="text-muted-foreground">{item.label}</dt>
            <dd className={cn("min-w-0 break-words font-medium tabular-nums", TEXT_TONE[item.tone])}>{item.text}</dd>
          </div>
        ))}
      </dl>
    );
  }
  if (model.type === "table") {
    if (model.rows.length === 0) {
      return <div className="text-xs text-muted-foreground" data-plugin-card-block="table">{model.empty}</div>;
    }
    return (
      <div data-plugin-card-block="table" data-plugin-card-table={blockId}>
        {model.title ? <div className="mb-1 text-xs font-medium">{model.title}</div> : null}
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full min-w-max text-xs">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                {model.columns.map((column) => (
                  <th key={column.key} className="px-2 py-1.5 font-medium">{column.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {model.rows.map((row) => (
                <tr key={row.key} className="border-b border-border/50 last:border-0" data-plugin-card-row={row.key}>
                  {row.cells.map((cell, index) => (
                    <td key={index} className={cn("max-w-[18rem] truncate px-2 py-1.5 tabular-nums", TEXT_TONE[cell.tone])} title={cell.text}>
                      {cell.text}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ul className="space-y-2 md:hidden">
          {model.rows.map((row) => (
            <li key={row.key} className="rounded-md border border-border/60 bg-muted/20 px-3 py-2" data-plugin-card-row={row.key}>
              <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
                {row.cells.map((cell, index) => (
                  <div key={index} className="contents">
                    <dt className="text-muted-foreground">{model.columns[index]?.label}</dt>
                    <dd className={cn("min-w-0 break-all font-medium tabular-nums", TEXT_TONE[cell.tone])}>{cell.text}</dd>
                  </div>
                ))}
              </dl>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (model.type === "list") {
    if (model.hidden) return null;
    return (
      <div data-plugin-card-block="list" data-plugin-card-list={blockId}>
        {model.title ? <div className="mb-1 text-xs font-medium">{model.title}</div> : null}
        {model.items.length === 0 ? (
          <div className="text-xs text-muted-foreground">{model.empty}</div>
        ) : (
          <ul className="divide-y divide-border/60 rounded-md border border-border/60">
            {model.items.map((item) => (
              <li key={item.key} className="flex items-start justify-between gap-3 px-3 py-2">
                <div className="min-w-0">
                  <div className="break-all font-mono text-xs font-medium">{item.title}</div>
                  {item.subtitle ? <div className="mt-0.5 break-words text-xs text-muted-foreground">{item.subtitle}</div> : null}
                </div>
                {item.trailing ? <MetaBadge tone={BADGE_TONE[item.tone]}>{item.trailing}</MetaBadge> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  if (model.type === "buttons") {
    return (
      <div className="flex flex-wrap gap-2" data-plugin-card-block="buttons">
        {model.items.map((item) => (
          <Button
            key={item.id}
            type="button"
            size="sm"
            variant={item.destructive ? "destructive" : "outline"}
            className="h-8 px-3 text-xs"
            disabled={busyAction === item.id}
            onClick={() => onAction(item.id, item.confirm)}
          >
            {item.label}
          </Button>
        ))}
      </div>
    );
  }
  return (
    <div className="text-xs text-warning">此卡片布局（{"layout" in model ? model.layout : model.type}）需要更新 TelePilot 才能显示。</div>
  );
}
