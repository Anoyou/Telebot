// Dashboard：系统状态总览 + 账号状态卡
//
// 顶部新加 SystemHealthCard：DB / alembic / Redis / providers / proxies / workers
// 用 30s 轮询自动刷新，让"配置改动 / 子服务挂掉"这类变化几十秒内可见。
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AccountSummaryCard } from "@/components/AccountSummaryCard";
import { SystemHealthCard } from "@/components/SystemHealthCard";
import { Spinner } from "@/components/ui/misc";
import { listAccounts } from "@/api/accounts";

export function Dashboard() {
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">系统概览</h1>
        <p className="text-sm text-muted-foreground">
          系统状态 + 多账号运行状态一览
        </p>
      </div>

      {/* 系统状态卡（DB / alembic / Redis / providers / proxies / workers）*/}
      <SystemHealthCard />

      {/* 账号状态卡 */}
      <section>
        <h2 className="mb-3 text-sm font-medium text-muted-foreground">
          账号状态
        </h2>
        {accountsQ.isLoading ? (
          <div className="flex h-24 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : accountsQ.data && accountsQ.data.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {accountsQ.data.map((a) => (
              <AccountSummaryCard key={a.id} account={a} />
            ))}
          </div>
        ) : (
          <Card>
            <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-sm text-muted-foreground">
              <span>尚未绑定任何 TG 账号</span>
              <Button asChild size="sm">
                <Link to="/accounts/new">立即绑定</Link>
              </Button>
            </CardContent>
          </Card>
        )}
      </section>
    </div>
  );
}
