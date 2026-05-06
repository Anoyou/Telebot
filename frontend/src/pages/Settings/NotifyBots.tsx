import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import {
  createNotifyBot,
  deleteNotifyBot,
  listNotifyBots,
  testNotifyBot,
  updateNotifyBot,
} from "@/api/notify_bots";
import { getErrMsg } from "@/lib/api";

const QK = ["notify-bots"] as const;

type FormState = {
  name: string;
  bot_token: string;
  default_chat_id: string;
  enabled: boolean;
};

const EMPTY_FORM: FormState = {
  name: "",
  bot_token: "",
  default_chat_id: "",
  enabled: true,
};

export function NotifyBots() {
  const qc = useQueryClient();
  const listQ = useQuery({ queryKey: QK, queryFn: listNotifyBots });
  const [form, setForm] = useState<FormState>(EMPTY_FORM);

  const createMut = useMutation({
    mutationFn: () =>
      createNotifyBot({
        name: form.name.trim(),
        bot_token: form.bot_token.trim(),
        default_chat_id: Number(form.default_chat_id),
        enabled: form.enabled,
      }),
    onSuccess: () => {
      toast.success("已创建通知 Bot");
      setForm(EMPTY_FORM);
      qc.invalidateQueries({ queryKey: QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const updateMut = useMutation({
    mutationFn: async (args: { id: number; enabled: boolean }) =>
      updateNotifyBot(args.id, { enabled: args.enabled }),
    onSuccess: () => {
      toast.success("已更新");
      qc.invalidateQueries({ queryKey: QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: async (id: number) => deleteNotifyBot(id),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const testMut = useMutation({
    mutationFn: async (id: number) => testNotifyBot(id),
    onSuccess: () => toast.success("测试消息已发送"),
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const canCreate = useMemo(() => {
    if (!form.name.trim() || !form.bot_token.trim() || !form.default_chat_id.trim()) {
      return false;
    }
    return /^-?\d+$/.test(form.default_chat_id.trim());
  }, [form]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">通知 Bot</CardTitle>
        <CardDescription>
          用 Telegram Bot API 发项目通知。Bot token 会加密保存，列表不会返回明文。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-3 md:grid-cols-4">
          <div className="space-y-1.5">
            <Label>名称</Label>
            <Input
              placeholder="default / alert"
              value={form.name}
              onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
            />
          </div>
          <div className="space-y-1.5 md:col-span-2">
            <Label>Bot Token</Label>
            <Input
              type="password"
              placeholder="123456:ABC-DEF..."
              value={form.bot_token}
              onChange={(e) =>
                setForm((p) => ({ ...p, bot_token: e.target.value }))
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label>默认 Chat ID</Label>
            <Input
              placeholder="-1001234567890"
              value={form.default_chat_id}
              onChange={(e) =>
                setForm((p) => ({ ...p, default_chat_id: e.target.value }))
              }
            />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Switch
            checked={form.enabled}
            onCheckedChange={(v) => setForm((p) => ({ ...p, enabled: v }))}
          />
          <span className="text-sm text-muted-foreground">创建后立即启用</span>
          <Button
            className="ml-auto"
            onClick={() => createMut.mutate()}
            disabled={!canCreate || createMut.isPending}
          >
            新建
          </Button>
        </div>

        {listQ.isLoading ? (
          <div className="flex h-20 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : (
          <div className="space-y-2">
            {(listQ.data || []).map((row) => (
              <div
                key={row.id}
                className="rounded-md border px-3 py-2 flex flex-col gap-2 md:flex-row md:items-center"
              >
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{row.name}</div>
                  <div className="text-xs text-muted-foreground">
                    chat_id={row.default_chat_id} · token={row.has_token ? "已配置" : "未配置"}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Switch
                    checked={row.enabled}
                    onCheckedChange={(v) =>
                      updateMut.mutate({ id: row.id, enabled: v })
                    }
                  />
                  <Button
                    variant="secondary"
                    onClick={() => testMut.mutate(row.id)}
                    disabled={!row.enabled || testMut.isPending}
                  >
                    测试
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => {
                      const next = prompt("可选：输入新 chat_id（留空不改）", String(row.default_chat_id));
                      if (next === null) return;
                      if (next.trim() && !/^-?\d+$/.test(next.trim())) {
                        toast.error("chat_id 必须是整数");
                        return;
                      }
                      updateNotifyBot(row.id, {
                        default_chat_id: next.trim() ? Number(next.trim()) : undefined,
                      })
                        .then(() => {
                          toast.success("已更新 chat_id");
                          qc.invalidateQueries({ queryKey: QK });
                        })
                        .catch((err) => toast.error(getErrMsg(err)));
                    }}
                  >
                    改 Chat ID
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() => {
                      if (!confirm(`确认删除 ${row.name} ?`)) return;
                      delMut.mutate(row.id);
                    }}
                    disabled={delMut.isPending}
                  >
                    删除
                  </Button>
                </div>
              </div>
            ))}
            {(listQ.data || []).length === 0 ? (
              <div className="text-sm text-muted-foreground">暂无通知 Bot 配置</div>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
