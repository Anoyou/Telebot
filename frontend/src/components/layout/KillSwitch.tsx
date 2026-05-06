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
  });
  const enabled = !!data?.enabled;

  const mut = useMutation({
    mutationFn: async (next: boolean) => {
      await api.post("/api/system/kill-switch", { enabled: next });
    },
    onSuccess: (_, next) => {
      toast.success(next ? "已开启紧急停用：所有 worker 已暂停" : "已恢复运行");
      qc.invalidateQueries({ queryKey: ["system", "kill-switch"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Button
      variant={enabled ? "outline" : "destructive"}
      size="sm"
      onClick={() => {
        if (mut.isPending) return;
        const next = !enabled;
        if (next && !confirm("确认要紧急停用所有账号？所有 worker 立即暂停。")) return;
        mut.mutate(next);
      }}
    >
      {enabled ? (
        <>
          <ShieldCheck className="mr-1 h-4 w-4" /> 恢复运行
        </>
      ) : (
        <>
          <ShieldAlert className="mr-1 h-4 w-4" /> 紧急停用
        </>
      )}
    </Button>
  );
}
