// 账号列表：卡片网格形式（移动端单列），含启停 / 详情 / 删除（二次确认）操作
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Power, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/misc";
import { AccountSummaryCard } from "@/components/AccountSummaryCard";
import {
  deleteAccount,
  listAccounts,
  pauseAccount,
  resumeAccount,
} from "@/api/accounts";
import { getErrMsg } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";

export function AccountList() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  const toggleMut = useMutation({
    mutationFn: async (vars: { aid: number; pause: boolean }) =>
      vars.pause ? pauseAccount(vars.aid) : resumeAccount(vars.aid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已下发指令");
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: deleteAccount,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已删除账号");
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">账号管理</h1>
          <p className="text-sm text-muted-foreground">
            每个账号 = 一个 session = 一个独立 worker 进程
          </p>
        </div>
        <Button onClick={() => nav("/accounts/new")}>
          <Plus className="mr-1 h-4 w-4" /> 新增账号
        </Button>
      </div>

      {isLoading ? (
        <div className="flex h-32 items-center justify-center">
          <Spinner className="text-primary" />
        </div>
      ) : data && data.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {data.map((a) => (
            <AccountSummaryCard
              key={a.id}
              account={a}
              footer={
                <div className="space-y-2 text-xs">
                  <div className="flex items-center justify-between text-muted-foreground">
                    <span>已启用 {a.enabled_features} 项</span>
                    <span title={formatDateTime(a.created_at)}>
                      {formatDateTime(a.created_at).slice(0, 10)}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 px-2"
                      onClick={() =>
                        toggleMut.mutate({
                          aid: a.id,
                          pause: a.status === "active",
                        })
                      }
                    >
                      <Power className="mr-1 h-3.5 w-3.5" />
                      {a.status === "active" ? "暂停" : "启动"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 px-2"
                      onClick={() => nav(`/accounts/${a.id}`)}
                    >
                      详情
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 px-2 text-destructive hover:text-destructive"
                      onClick={() => {
                        const label =
                          a.display_name ||
                          (a.tg_username ? `@${a.tg_username}` : `#${a.id}`);
                        if (
                          confirm(
                            `确认删除账号 ${label}？此操作会撤销 session 并清空配置。`,
                          )
                        )
                          delMut.mutate(a.id);
                      }}
                    >
                      <Trash2 className="mr-1 h-3.5 w-3.5" /> 删除
                    </Button>
                  </div>
                </div>
              }
            />
          ))}
        </div>
      ) : (
        <p className="rounded-lg border bg-card py-12 text-center text-sm text-muted-foreground">
          尚未绑定账号，
          <Link to="/accounts/new" className="text-primary hover:underline">
            立即新增
          </Link>
        </p>
      )}
    </div>
  );
}
