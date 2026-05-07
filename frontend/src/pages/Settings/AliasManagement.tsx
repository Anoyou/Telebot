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

import {
  createAlias,
  deleteAlias,
  getAliases,
  updateAlias,
} from "@/api/alias";
import type { CommandAliasResponse } from "@/types/alias";
import { listAccounts } from "@/api/accounts";
import { getErrMsg } from "@/lib/api";

const QK = ["aliases"] as const;

type FormState = {
  alias: string;
  target: string;
  account_id: string;
};

const EMPTY_FORM: FormState = {
  alias: "",
  target: "",
  account_id: "",
};

export function AliasManagement() {
  const qc = useQueryClient();
  const listQ = useQuery<CommandAliasResponse[]>({
    queryKey: QK,
    queryFn: () => getAliases(),
  });
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: () => listAccounts(),
  });

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<number | null>(null);

  const createMut = useMutation({
    mutationFn: () =>
      createAlias({
        alias: form.alias.trim(),
        target: form.target.trim(),
        account_id: form.account_id ? Number(form.account_id) : undefined,
      }),
    onSuccess: () => {
      toast.success("已创建命令别名");
      setForm(EMPTY_FORM);
      qc.invalidateQueries({ queryKey: QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const updateMut = useMutation({
    mutationFn: async (id: number) => {
      await updateAlias(id, {
        target: form.target.trim(),
        account_id: form.account_id ? Number(form.account_id) : undefined,
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
    mutationFn: async (id: number) => deleteAlias(id),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const startEdit = (alias: CommandAliasResponse) => {
    setEditingId(alias.id);
    setForm({
      alias: alias.alias,
      target: alias.target,
      account_id: alias.account_id ? String(alias.account_id) : "",
    });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
  };

  const handleDelete = (id: number) => {
    if (!window.confirm("确定要删除这个命令别名吗？此操作不可撤销。")) return;
    deleteMut.mutate(id);
  };

  const canSave = useMemo(() => {
    if (!form.alias.trim() || !form.target.trim()) return false;
    if (form.account_id && isNaN(Number(form.account_id))) return false;
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
        <CardTitle className="text-base">命令别名管理</CardTitle>
        <CardDescription>
          创建命令别名，支持多词别名和参数透传。留空账号表示全局别名。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* 创建/编辑表单 */}
        <div className="space-y-4 rounded-lg border p-4">
          <h3 className="text-sm font-semibold">
            {editingId ? "编辑命令别名" : "添加命令别名"}
          </h3>

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label>别名 *</Label>
              <Input
                value={form.alias}
                onChange={(e) =>
                  setForm((f) => ({ ...f, alias: e.target.value }))
                }
                placeholder="如: fy 或 fy zh"
                disabled={editingId !== null}
              />
            </div>

            <div className="space-y-1.5">
              <Label>目标命令 *</Label>
              <Input
                value={form.target}
                onChange={(e) =>
                  setForm((f) => ({ ...f, target: e.target.value }))
                }
                placeholder="如: translate 或 translate zh"
              />
            </div>

            <div className="space-y-1.5">
              <Label>账号（可选）</Label>
              <select
                className="w-full rounded-md border px-3 py-2 text-sm"
                value={form.account_id}
                onChange={(e) =>
                  setForm((f) => ({ ...f, account_id: e.target.value }))
                }
              >
                <option value="">全局（所有账号）</option>
                {accountsQ.data?.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {acc.display_name || acc.phone}
                  </option>
                ))}
              </select>
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
          <h3 className="text-sm font-semibold">命令别名列表</h3>
          {!listQ.data || listQ.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无命令别名</p>
          ) : (
            <div className="space-y-2">
              {listQ.data.map((alias) => (
                <div
                  key={alias.id}
                  className="flex items-center justify-between rounded-lg border p-3"
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <code className="rounded bg-muted px-1.5 py-0.5 text-sm">
                        {alias.alias}
                      </code>
                      <span className="text-sm text-muted-foreground">→</span>
                      <code className="rounded bg-muted px-1.5 py-0.5 text-sm">
                        {alias.target}
                      </code>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {alias.account_id
                        ? `仅账号 ID: ${alias.account_id}`
                        : "全局别名"}
                    </p>
                  </div>
                  <div className="flex gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => startEdit(alias)}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleDelete(alias.id)}
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
