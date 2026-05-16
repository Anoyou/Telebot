// 顶部紧急停用按钮：调 POST /api/system/kill-switch 切换全局总闸
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, getErrMsg } from "@/lib/api";

interface KillSwitchState {
  enabled: boolean;
}

async function fetchKillSwitch(): Promise<KillSwitchState> {
  const { data } = await api.get<KillSwitchState>("/api/system/kill-switch");
  return data;
}

export function KillSwitch() {
  const qc = useQueryClient();
  // 实时显示总闸状态；轻量轮询：30s 刷新
  const { data } = useQuery({
    queryKey: ["system", "kill-switch"],
    queryFn: fetchKillSwitch,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
  const enabled = !!data?.enabled;

  const mut = useMutation({
    mutationFn: async (next: boolean) => {
      await api.post("/api/system/kill-switch", { enabled: next });
    },
    onSuccess: (_, next) => {
      toast.success(next ? "已开启紧急停用：所有账号 worker 已停止" : "已恢复运行");
      qc.invalidateQueries({ queryKey: ["system", "kill-switch"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Button
      variant={enabled ? "outline" : "destructive"}
      size="sm"
      className="h-10 w-10 px-0 sm:h-9 sm:w-auto sm:px-3"
      onClick={() => {
        if (mut.isPending) return;
        const next = !enabled;
        if (next && !confirm("确认要紧急停用所有账号？所有 worker 会立即停止。")) return;
        mut.mutate(next);
      }}
    >
      {enabled ? (
        <>
          <ShieldCheck className="h-4 w-4 sm:mr-1" />
          <span className="hidden sm:inline">恢复运行</span>
        </>
      ) : (
        <>
          <ShieldAlert className="h-4 w-4 sm:mr-1" />
          <span className="hidden sm:inline">紧急停用</span>
        </>
      )}
    </Button>
  );
}
