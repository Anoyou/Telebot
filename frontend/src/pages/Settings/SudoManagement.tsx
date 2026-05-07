import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";

import {
  createSudoUser,
  deleteSudoUser,
  getSudoUsers,
  updateSudoUser,
} from "@/api/sudo";
import type { SudoUserResponse } from "@/types/sudo";
import { listAccounts } from "@/api/accounts";
import { getErrMsg } from "@/lib/api";

const QK = ["sudo-users"] as const;

type FormState = {
  account_id: string;
  tg_user_id: string;
  display_name: string;
  allowed_chat_ids: string;
  allowed_commands: string;
};

const EMPTY_FORM: FormState = {
  account_id: "",
  tg_user_id: "",
  display_name: "",
  allowed_chat_ids: "",
  allowed_commands: "",
};

export function SudoManagement() {
  const qc = useQueryClient();
  const listQ = useQuery<SudoUserResponse[]>({
    queryKey: QK,
    queryFn: () => getSudoUsers(),
  });
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: () => listAccounts(),
  });

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<number | null>(null);

  const createMut = useMutation({
    mutationFn: () =>
      createSudoUser({
        account_id: Number(form.account_id),
        tg_user_id: Number(form.tg_user_id),
        display_name: form.display_name.trim() || undefined,
        allowed_chat_ids: form.allowed_chat_ids.trim()
          ? form.allowed_chat_ids.split(",").map((s) => Number(s.trim()))
          : undefined,
        allowed_commands: form.allowed_commands.trim()
          ? form.allowed_commands.split(",").map((s) => s.trim())
          : undefined,
      }),
    onSuccess: () => {
      toast.success("已创建 Sudo 用户");
      setForm(EMPTY_FORM);
      qc.invalidateQueries({ queryKey: QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const updateMut = useMutation({
    mutationFn: async (id: number) => {
      await updateSudoUser(id, {
        display_name: form.display_name.trim() || undefined,
        allowed_chat_ids: form.allowed_chat_ids.trim()
          ? form.allowed_chat_ids.split(",").map((s) => Number(s.trim()))
          : undefined,
        allowed_commands: form.allowed_commands.trim()
          ? form.allowed_commands.split(",").map((s) => s.trim())
          : undefined,
      });
    },
    onSuccess: () => {
      toast.success("已更新");
      setEditingId(null);
      setForm(EMPTY_FORM);
      qc.invalidateQueries({ queryKey: QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: number) => deleteSudoUser(id),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const startEdit = (user: SudoUserResponse) => {
    setEditingId(user.id);
    setForm({
      account_id: String(user.account_id),
      tg_user_id: String(user.tg_user_id),
      display_name: user.display_name || "",
      allowed_chat_ids: user.allowed_chat_ids?.join(", ") || "",
      allowed_commands: user.allowed_commands?.join(", ") || "",
    });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
  };

  const handleDelete = (id: number) => {
    if (!window.confirm("确定要删除这个 Sudo 用户吗？此操作不可撤销。")) return;
    deleteMut.mutate(id);
  };

  const canSave = useMemo(() => {
    if (!form.account_id || !form.tg_user_id) return false;
    if (isNaN(Number(form.account_id)) || isNaN(Number(form.tg_user_id)))
      return false;
    return true;
  }, [form]);

  if (listQ.isLoading || accountsQ.isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Sudo 用户管理</CardTitle>
        <CardDescription>
          授权其他 Telegram 用户通过独立前缀触发命令。留空表示允许所有对话/命令。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* 创建/编辑表单 */}
        <div className="space-y-4 rounded-lg border p-4">
          <h3 className="text-sm font-semibold">
            {editingId ? "编辑 Sudo 用户" : "添加 Sudo 用户"}
          </h3>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>账号 *</Label>
              <select
                className="w-full rounded-md border px-3 py-2 text-sm"
                value={form.account_id}
                onChange={(e) =>
                  setForm((f) => ({ ...f, account_id: e.target.value }))
                }
              >
                <option value="">请选择账号</option>
                {accountsQ.data?.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {acc.display_name || acc.phone}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <Label>Telegram 用户 ID *</Label>
              <Input
                type="number"
                value={form.tg_user_id}
                onChange={(e) =>
                  setForm((f) => ({ ...f, tg_user_id: e.target.value }))
                }
                placeholder="123456789"
              />
            </div>

            <div className="space-y-1.5">
              <Label>显示名称</Label>
              <Input
                value={form.display_name}
                onChange={(e) =>
                  setForm((f) => ({ ...f, display_name: e.target.value }))
                }
                placeholder="可选"
              />
            </div>

            <div className="space-y-1.5">
              <Label>允许的对话 ID（逗号分隔）</Label>
              <Input
                value={form.allowed_chat_ids}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    allowed_chat_ids: e.target.value,
                  }))
                }
                placeholder="留空=所有对话, 如: -100123, -100456"
              />
            </div>

            <div className="space-y-1.5 sm:col-span-2">
              <Label>允许的命令（逗号分隔）</Label>
              <Input
                value={form.allowed_commands}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    allowed_commands: e.target.value,
                  }))
                }
                placeholder="留空=所有命令, 如: help, ping, fwd"
              />
            </div>
          </div>

          <div className="flex gap-2">
            <Button
              onClick={() => {
                if (editingId) {
                  updateMut.mutate(editingId);
                } else {
                  createMut.mutate();
                }
              }}
              disabled={!canSave || createMut.isPending || updateMut.isPending}
            >
              {editingId ? "更新" : "添加"}
            </Button>
            {editingId && (
              <Button variant="outline" onClick={cancelEdit}>
                取消
              </Button>
            )}
          </div>
        </div>

        {/* 列表 */}
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Sudo 用户列表</h3>
          {!listQ.data || listQ.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无 Sudo 用户</p>
          ) : (
            <div className="space-y-2">
              {listQ.data.map((user) => (
                <div
                  key={user.id}
                  className="flex items-center justify-between rounded-lg border p-3"
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm">
                        TG User ID: {user.tg_user_id}
                      </span>
                      {user.display_name && (
                        <Badge variant="secondary">{user.display_name}</Badge>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      账号 ID: {user.account_id}
                    </p>
                    {user.allowed_chat_ids && user.allowed_chat_ids.length > 0 && (
                      <p className="text-xs text-muted-foreground">
                        允许对话: {user.allowed_chat_ids.join(", ")}
                      </p>
                    )}
                    {user.allowed_commands && user.allowed_commands.length > 0 && (
                      <p className="text-xs text-muted-foreground">
                        允许命令: {user.allowed_commands.join(", ")}
                      </p>
                    )}
                  </div>
                  <div className="flex gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => startEdit(user)}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleDelete(user.id)}
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
