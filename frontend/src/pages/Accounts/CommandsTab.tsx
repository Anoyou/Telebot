// 账号详情 → 命令 tab：列出全量模板 + 勾选启用/禁用
//
// 一行一条模板：左侧名称 + 类型徽章 + 描述；右侧 Switch
// 模板内容由「通用模板」页统一管理；这里仅负责"是否在该账号上启用"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/misc";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import {
  disableAccountCommand,
  enableAccountCommand,
  listAccountCommands,
} from "@/api/commands";
import { getSystemSettings } from "@/api/system";
import type { CommandTemplateType } from "@/api/types";
import { getErrMsg } from "@/lib/api";

const TYPE_LABELS: Record<CommandTemplateType, string> = {
  reply_text: "回复文本",
  forward_to: "转发到",
  run_plugin: "调插件",
  ai: "AI",
};

export function CommandsTab({ aid }: { aid: number }) {
  const qc = useQueryClient();

  const listQ = useQuery({
    queryKey: ["account", aid, "commands"],
    queryFn: () => listAccountCommands(aid),
  });
  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const cmdPrefix = settingsQ.data?.command_prefix || ",";

  const toggleMut = useMutation({
    mutationFn: async (vars: { templateId: number; enabled: boolean }) =>
      vars.enabled
        ? enableAccountCommand(aid, vars.templateId)
        : disableAccountCommand(aid, vars.templateId),
    onSuccess: (_d, vars) => {
      toast.success(`${vars.enabled ? "已启用" : "已禁用"}（worker 热加载）`);
      qc.invalidateQueries({ queryKey: ["account", aid, "commands"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">自定义命令</CardTitle>
            <CardDescription>
              勾选模板即在该账号生效，TG 内即可用 <code>{cmdPrefix}name</code> 触发；模板内容请到「通用模板 → 自定义命令」管理
            </CardDescription>
          </div>
          <Button asChild variant="outline" size="sm">
            <Link to="/templates">
              <Plus className="mr-1 h-4 w-4" /> 管理模板
            </Link>
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {listQ.isLoading ? (
          <div className="flex h-20 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : listQ.data && listQ.data.length > 0 ? (
          <ul className="divide-y">
            {listQ.data.map((item) => {
              const t = item.template;
              return (
                <li
                  key={t.id}
                  className="flex items-center justify-between gap-3 py-3"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm">{cmdPrefix}{t.name}</span>
                      <Badge variant="secondary">
                        {TYPE_LABELS[t.type] || t.type}
                      </Badge>
                    </div>
                    <div className="mt-0.5 truncate text-xs text-muted-foreground">
                      {t.description || "—"}
                    </div>
                  </div>
                  <Switch
                    checked={item.enabled}
                    onCheckedChange={(v) =>
                      toggleMut.mutate({ templateId: t.id, enabled: v })
                    }
                    disabled={toggleMut.isPending}
                  />
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="rounded-md border border-dashed py-8 text-center text-xs text-muted-foreground">
            尚未创建任何命令模板。先到「通用模板 → 自定义命令」新建一个
          </p>
        )}
      </CardContent>
    </Card>
  );
}
