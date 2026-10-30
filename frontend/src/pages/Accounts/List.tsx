// 账号列表页 /accounts（第 20 轮 Web 信息架构第二批）：账号第一次有了侧栏入口（「管理」组）。
// 复用概览「账号 Worker」浮层里的账号卡；概览的 /overview?accounts=1 浮层继续可用。
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Plus, Users } from "lucide-react";

import { listAccounts } from "@/api/accounts";
import { AccountSummaryCard } from "@/components/AccountSummaryCard";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader, PageShell } from "@/components/layout/PageScaffold";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/misc";
import { SignalPill } from "@/components/ui/status";

export function AccountsListPage() {
  const accountsQ = useQuery({ queryKey: ["accounts"], queryFn: listAccounts });
  const accounts = accountsQ.data ?? [];
  const active = accounts.filter((account) => account.status === "active").length;

  return (
    <PageShell>
      <PageHeader
        icon={Users}
        title="账号"
        description="所有 Telegram 账号的运行状态、出网信息与详情入口；点账号进入账号详情配置插件、管理 Bot、限流与代理。"
        signals={
          accountsQ.data ? (
            <>
              <SignalPill tone={active === accounts.length ? "success" : "warn"} label="运行中" value={`${active}/${accounts.length}`} />
            </>
          ) : null
        }
        actions={
          <Button asChild size="sm">
            <Link to="/accounts/new">
              <Plus className="mr-1 h-4 w-4" />
              新增账号
            </Link>
          </Button>
        }
      />
      {accountsQ.isLoading ? (
        <div role="status" aria-label="账号列表加载中" className="grid gap-3 lg:grid-cols-2">
          {[0, 1].map((item) => <Skeleton key={item} className="h-48 rounded-lg" />)}
        </div>
      ) : accountsQ.isError ? (
        <ErrorState title="账号列表读取失败" error={accountsQ.error} onRetry={() => void accountsQ.refetch()} />
      ) : accounts.length === 0 ? (
        <EmptyState
          icon={Users}
          size="lg"
          title="还没有绑定账号"
          description="绑定第一个 Telegram 账号后，就可以按账号启用插件、配置管理 Bot 和限流。"
          action={
            <Button asChild size="sm">
              <Link to="/accounts/new?guide=1">
                <Plus className="mr-1 h-4 w-4" />
                新增账号
              </Link>
            </Button>
          }
        />
      ) : (
        <div data-accounts-list className={accounts.length === 1 ? "grid max-w-[32rem] gap-3" : "grid gap-3 lg:grid-cols-2"}>
          {accounts.map((account) => (
            <AccountSummaryCard key={account.id} account={account} />
          ))}
        </div>
      )}
    </PageShell>
  );
}
