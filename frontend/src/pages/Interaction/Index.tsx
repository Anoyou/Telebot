import { useEffect, useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  MessageSquare,
  RefreshCw,
  Route,
  ShieldCheck,
} from "lucide-react";

import { getInteractionBotConfig } from "@/api/accountBots";
import { listAccounts } from "@/api/accounts";
import type {
  AccountBotInteractionConfig,
  AccountBotInteractionRule,
  AccountSummary,
} from "@/api/types";
import { AccountStatusBadge } from "@/components/AccountStatusBadge";
import { PageHeader, PageShell } from "@/components/layout/PageScaffold";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { SignalPill } from "@/components/ui/status";
import { BotTab } from "@/pages/Accounts/BotTab";

function accountLabel(account: AccountSummary): string {
  const name = account.display_name?.trim();
  const handle = account.tg_username ? `@${account.tg_username}` : null;
  return [name || handle || account.phone, name || handle ? account.phone : null]
    .filter(Boolean)
    .join(" · ");
}

function countRuleChats(rules: AccountBotInteractionRule[]): number {
  return new Set(
    rules.flatMap((rule) =>
      (rule.chat_ids ?? []).filter((chatId): chatId is number => Number.isFinite(chatId)),
    ),
  ).size;
}

function runtimeLabel(config?: AccountBotInteractionConfig): string {
  if (!config) return "读取中";
  if (config.interaction_running) return "运行中";
  if (config.enabled) return "未运行";
  return "未启用";
}

function runtimeTone(config?: AccountBotInteractionConfig): "success" | "warn" | "neutral" {
  if (config?.interaction_running) return "success";
  if (config?.enabled) return "warn";
  return "neutral";
}

function lastUpdateLabel(config?: AccountBotInteractionConfig): string {
  if (!config?.interaction_last_update_id) return "暂无触发";
  return `update #${config.interaction_last_update_id}`;
}

export function InteractionIndex() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();

  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: () => listAccounts(),
  });

  const accounts = accountsQ.data ?? [];
  const aidParam = Number(searchParams.get("aid"));
  const selectedAccount = useMemo(() => {
    if (!accounts.length) return null;
    const byParam = accounts.find((account) => account.id === aidParam);
    return byParam ?? accounts[0];
  }, [accounts, aidParam]);
  const selectedAid = selectedAccount?.id ?? null;

  useEffect(() => {
    if (!selectedAid) return;
    if (selectedAid === aidParam) return;
    const next = new URLSearchParams(searchParams);
    next.set("aid", String(selectedAid));
    setSearchParams(next, { replace: true });
  }, [aidParam, searchParams, selectedAid, setSearchParams]);

  const interactionQ = useQuery({
    queryKey: ["account", selectedAid, "interaction-bot"],
    queryFn: () => getInteractionBotConfig(selectedAid as number),
    enabled: selectedAid !== null,
  });

  const config = interactionQ.data;
  const rules = config?.rules ?? [];
  const activeRules = rules.filter((rule) => rule.enabled).length;
  const chatCoverage = countRuleChats(rules);
  const hasInteractionToken = Boolean(config?.has_interaction_bot_token);
  const lastError = config?.interaction_last_error?.trim();

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["accounts"] });
    if (selectedAid !== null) {
      void queryClient.invalidateQueries({ queryKey: ["account", selectedAid, "interaction-bot"] });
    }
  };

  if (accountsQ.isLoading) {
    return (
      <PageShell>
        <PageHeader
          icon={Bot}
          title="交互中心"
          description="正在读取账号与交互 Bot 配置。"
        />
        <div className="flex h-36 items-center justify-center rounded-lg border bg-card">
          <Spinner className="text-primary" />
        </div>
      </PageShell>
    );
  }

  return (
    <PageShell>
      <PageHeader
        icon={Bot}
        title="交互中心"
        description="按账号管理交互 Bot、插件玩法入口、触发规则和会话运行状态。"
        actions={
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={refresh}
              disabled={accountsQ.isFetching || interactionQ.isFetching}
            >
              <RefreshCw className={accountsQ.isFetching || interactionQ.isFetching ? "mr-1 h-4 w-4 animate-spin" : "mr-1 h-4 w-4"} />
              刷新
            </Button>
            {selectedAid !== null ? (
              <Button asChild variant="outline" size="sm">
                <Link to={`/accounts/${selectedAid}?tab=interaction-bot`}>
                  账号详情入口
                  <ArrowRight className="ml-1 h-4 w-4" />
                </Link>
              </Button>
            ) : null}
          </>
        }
      />

      {accounts.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <AlertTriangle className="h-4 w-4 text-amber-500" />
              暂无账号
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
            <span>交互中心需要先绑定一个 Telegram 账号，再配置对应的交互 Bot 和规则。</span>
            <Button asChild size="sm">
              <Link to="/accounts/new">添加账号</Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          <section className="grid gap-4 rounded-lg border bg-card p-4 shadow-sm lg:grid-cols-[minmax(280px,420px)_minmax(0,1fr)]">
            <div className="space-y-2">
              <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                当前账号 / 交互 Bot
              </div>
              <Select
                value={selectedAid ? String(selectedAid) : ""}
                onChange={(event) => {
                  const next = new URLSearchParams(searchParams);
                  next.set("aid", event.target.value);
                  setSearchParams(next);
                }}
              >
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {accountLabel(account)}
                  </option>
                ))}
              </Select>
              {selectedAccount ? (
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <AccountStatusBadge status={selectedAccount.status} />
                  <span>ID {selectedAccount.id}</span>
                  {selectedAccount.tg_user_id ? <span>TG {selectedAccount.tg_user_id}</span> : null}
                </div>
              ) : null}
            </div>

            <div className="flex flex-wrap items-center gap-2 lg:justify-end">
              <SignalPill
                tone={config?.enabled ? "primary" : "neutral"}
                label="交互总闸"
                value={config?.enabled ? "已启用" : "未启用"}
              />
              <SignalPill
                tone={hasInteractionToken ? "success" : "warn"}
                label="Bot Token"
                value={hasInteractionToken ? "已配置" : "待配置"}
              />
              <SignalPill
                tone={runtimeTone(config)}
                label="监听状态"
                value={runtimeLabel(config)}
              />
              <SignalPill
                tone={activeRules > 0 ? "primary" : "neutral"}
                label="启用规则"
                value={`${activeRules}/${rules.length} · ${chatCoverage} 群`}
              />
              <SignalPill
                tone={config?.interaction_last_update_id ? "success" : "neutral"}
                label="最近触发"
                value={lastUpdateLabel(config)}
              />
              <SignalPill
                tone={lastError ? "danger" : "success"}
                label="最近错误"
                value={lastError ? "有错误" : "无错误"}
              />
            </div>
          </section>

          <Card className="overflow-hidden">
            <CardHeader className="border-b bg-muted/30">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Route className="h-4 w-4 text-primary" />
                    规则与插件入口
                  </CardTitle>
                  <p className="mt-1 text-sm text-muted-foreground">
                    新增规则、选择插件入口、配置触发词和参数都在这里完成，保存后当前账号立即使用这套交互规则。
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {config?.interaction_bot_username ? (
                    <Badge variant="outline" className="h-7">
                      <MessageSquare className="mr-1 h-3.5 w-3.5" />
                      @{config.interaction_bot_username}
                    </Badge>
                  ) : null}
                  <Badge variant={lastError ? "destructive" : "secondary"} className="h-7">
                    <ShieldCheck className="mr-1 h-3.5 w-3.5" />
                    Contract Guard
                  </Badge>
                </div>
              </div>
            </CardHeader>
            <CardContent className="p-3 sm:p-4">
              {selectedAid !== null ? <BotTab aid={selectedAid} mode="interaction" presentation="center" /> : null}
            </CardContent>
          </Card>
        </>
      )}
    </PageShell>
  );
}
