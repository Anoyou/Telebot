import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Bot,
  KeyRound,
  Loader2,
  RefreshCw,
  Send,
  ShieldCheck,
  Trash2,
  UserPlus,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/misc";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  createAccountBotUser,
  deleteAccountBotUser,
  getAccountBot,
  listAccountBotUsers,
  restartAccountBotRuntime,
  testAccountBot,
  updateAccountBot,
  updateAccountBotUser,
} from "@/api/accountBots";
import type {
  AccountBotRemotePluginPolicy,
  AccountBotRole,
  AccountBotUserCreate,
} from "@/api/types";
import { getErrMsg } from "@/lib/api";

const MASKED_SECRET_PLACEHOLDER = "••••••••••••••••";
import { formatDateTime } from "@/lib/utils";

const ROLE_META: Record<AccountBotRole, { label: string; desc: string }> = {
  viewer: { label: "viewer", desc: "只读查看" },
  operator: { label: "operator", desc: "启停常用功能" },
  admin: { label: "admin", desc: "危险操作确认" },
};

const HELP_PREVIEW = `/start  打开主菜单
/status 查看账号、worker 与最近错误
/features 查看并启停账号功能
/commands 查看并启停自定义指令模板
/plugins 查看模块入口
/rules 查看规则，scheduler 规则可手动执行
/logs 查看最近运行日志
/pause /resume 暂停或恢复账号
/restart 重启账号 worker（admin + 二次确认）`;

const DEFAULT_REMOTE_POLICY: AccountBotRemotePluginPolicy = {
  enabled: false,
  install: false,
  update: false,
  uninstall: false,
  enable_disable: false,
};

export function BotTab({ aid }: { aid: number }) {
  const qc = useQueryClient();
  const [enabled, setEnabled] = useState(false);
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [remotePolicy, setRemotePolicy] = useState<AccountBotRemotePluginPolicy>(
    DEFAULT_REMOTE_POLICY,
  );
  const [newUser, setNewUser] = useState<AccountBotUserCreate>({
    tg_user_id: 0,
    display_name: "",
    role: "viewer",
    notify_enabled: true,
    enabled: true,
  });

  const botQ = useQuery({
    queryKey: ["account", aid, "bot"],
    queryFn: () => getAccountBot(aid),
    enabled: !!aid,
  });
  const usersQ = useQuery({
    queryKey: ["account", aid, "bot", "users"],
    queryFn: () => listAccountBotUsers(aid),
    enabled: !!aid,
  });

  useEffect(() => {
    if (botQ.data) {
      setEnabled(botQ.data.enabled);
      setClearToken(false);
      setToken("");
      setRemotePolicy(botQ.data.remote_plugin_policy ?? DEFAULT_REMOTE_POLICY);
    }
  }, [botQ.data?.enabled, botQ.data?.has_token, botQ.data?.remote_plugin_policy]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["account", aid, "bot"] });
    qc.invalidateQueries({ queryKey: ["account", aid, "bot", "users"] });
  };

  const saveMut = useMutation({
    mutationFn: () =>
      updateAccountBot(aid, {
        enabled,
        clear_token: clearToken,
        bot_token: token.trim() || null,
        remote_plugin_policy: remotePolicy,
      }),
    onSuccess: () => {
      toast.success("Bot 配置已保存");
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const testMut = useMutation({
    mutationFn: () => testAccountBot(aid),
    onSuccess: () => toast.success("测试消息已发送"),
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const restartMut = useMutation({
    mutationFn: () => restartAccountBotRuntime(aid),
    onSuccess: () => {
      toast.success("Bot polling runtime 已重启");
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const addUserMut = useMutation({
    mutationFn: () => {
      if (!newUser.tg_user_id) throw new Error("请填写 Telegram 用户 ID");
      return createAccountBotUser(aid, {
        ...newUser,
        display_name: newUser.display_name?.trim() || null,
      });
    },
    onSuccess: () => {
      toast.success("授权用户已添加");
      setNewUser({
        tg_user_id: 0,
        display_name: "",
        role: "viewer",
        notify_enabled: true,
        enabled: true,
      });
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const updateUserMut = useMutation({
    mutationFn: (vars: { uid: number; patch: Partial<AccountBotUserCreate> }) =>
      updateAccountBotUser(aid, vars.uid, vars.patch),
    onSuccess: () => invalidate(),
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteUserMut = useMutation({
    mutationFn: (uid: number) => deleteAccountBotUser(aid, uid),
    onSuccess: () => {
      toast.success("授权用户已删除");
      invalidate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (botQ.isLoading || usersQ.isLoading) {
    return (
      <div className="flex h-28 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  const bot = botQ.data;
  const users = usersQ.data ?? [];

  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Bot className="h-4 w-4" /> 账号 Bot 配置
              <Badge variant="destructive" className="ml-1">
                危险操作需 Telegram 内二次确认
              </Badge>
            </CardTitle>
            <CardDescription>
              每个账号绑定一个普通 Bot，互相隔离授权和通知。Bot Token 不会回显。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              安全提示：涉及重启、安装等危险操作时，需在 Telegram 内完成二次确认后才会执行。
            </div>
            <div className="space-y-3 rounded-md border border-red-300/70 bg-red-50 px-3 py-3 dark:border-red-400/40 dark:bg-red-950/30">
              <div className="flex items-center gap-2">
                <div className="text-sm font-medium text-red-900 dark:text-red-100">远程模块高风险开关（admin）</div>
                <Dialog>
                  <DialogTrigger asChild>
                    <button
                      type="button"
                      className="text-xs font-medium text-red-700 underline underline-offset-2 hover:text-red-800 dark:text-red-200 dark:hover:text-red-100"
                    >
                      详情
                    </button>
                  </DialogTrigger>
                  <DialogContent className="max-h-[85vh] w-[calc(100vw-2rem)] max-w-xl overflow-y-auto rounded-xl">
                    <DialogHeader>
                      <DialogTitle className="text-base">远程模块高风险开关说明</DialogTitle>
                      <DialogDescription>
                        这是账号 Bot 联动里的高风险远程模块总闸，采用“Web 策略开关 + Telegram 内二次确认”双重防护。
                      </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-3 text-sm text-foreground/90">
                      <div><strong>总开关 enabled：</strong>关闭时所有高风险远程模块动作都不允许从 TG Bot 发起；开启后才会继续检查子开关。</div>
                      <div><strong>允许 install：</strong>控制 <code>/plugins install &lt;git-url&gt;</code>。</div>
                      <div><strong>允许 update：</strong>控制 <code>/plugins update &lt;name&gt;</code>。</div>
                      <div><strong>允许 uninstall：</strong>控制 <code>/plugins uninstall &lt;name&gt;</code>。</div>
                      <div><strong>允许第三方 enable/disable：</strong>控制第三方远程模块启停操作。</div>
                      <div><strong>权限边界：</strong>仅 <code>admin</code> 可操作；<code>viewer/operator</code> 会被拦截。</div>
                      <div><strong>执行机制：</strong>开关只代表“允许发起请求”，真正执行前仍需在 Telegram 内二次确认，且确认有时效并绑定发起人。</div>
                    </div>
                  </DialogContent>
                </Dialog>
              </div>
              <div className="text-xs text-red-800 dark:text-red-200">
                默认全部关闭；即使开启后，Telegram 内仍需二次确认才会执行 install/update/uninstall/第三方启停。
              </div>
              <div className="grid gap-2 text-sm md:grid-cols-2">
                {[
                  ["enabled", "总开关"],
                  ["install", "允许 install"],
                  ["update", "允许 update"],
                  ["uninstall", "允许 uninstall"],
                  ["enable_disable", "允许第三方 enable/disable"],
                ].map(([key, label]) => (
                  <label
                    key={key}
                    className="flex items-center justify-between rounded border border-red-200/70 bg-white/95 px-3 py-2 text-red-950 dark:border-red-300/30 dark:bg-red-950/10 dark:text-red-100"
                  >
                    <span>{label}</span>
                    <Switch
                      checked={remotePolicy[key as keyof AccountBotRemotePluginPolicy]}
                      onCheckedChange={(checked) =>
                        setRemotePolicy((prev) => ({
                          ...prev,
                          [key]: checked,
                        }))
                      }
                    />
                  </label>
                ))}
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>运行状态</Label>
                <div className="flex h-10 items-center gap-2 rounded-md border px-3 text-sm">
                  <Badge variant={bot?.enabled ? "default" : "secondary"}>
                    {bot?.enabled ? "已启用" : "未启用"}
                  </Badge>
                  <span className="font-mono text-muted-foreground">
                    {bot?.status ?? "disabled"}
                  </span>
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>Bot 用户名</Label>
                <div className="flex h-10 items-center rounded-md border px-3 text-sm">
                  {bot?.username ? `@${bot.username}` : "保存 token 后自动读取"}
                </div>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="account-bot-token">Bot Token</Label>
              <div className="flex gap-2">
                <Input
                  id="account-bot-token"
                  type="password"
                  autoComplete="off"
                  placeholder={bot?.has_token ? MASKED_SECRET_PLACEHOLDER : "123456:ABC-DEF..."}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                />
                <Button
                  type="button"
                  variant={clearToken ? "destructive" : "outline"}
                  onClick={() => setClearToken((v) => !v)}
                >
                  <KeyRound className="mr-1 h-4 w-4" />
                  {clearToken ? "将清空" : "清空"}
                </Button>
              </div>
            </div>

            {bot?.last_error ? (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                {bot.last_error}
              </div>
            ) : null}

            <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={enabled} onCheckedChange={setEnabled} />
                启用 Bot polling
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={() => testMut.mutate()}
                  disabled={testMut.isPending || !bot?.has_token}
                >
                  {testMut.isPending ? (
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="mr-1 h-4 w-4" />
                  )}
                  测试发送
                </Button>
                <Button
                  variant="outline"
                  onClick={() => restartMut.mutate()}
                  disabled={restartMut.isPending}
                >
                  <RefreshCw className="mr-1 h-4 w-4" />
                  重启 runtime
                </Button>
                <Button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
                  {saveMut.isPending ? (
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  ) : null}
                  保存配置
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheck className="h-4 w-4" /> 可操作范围
            </CardTitle>
            <CardDescription>GUI 是完整控制台，Bot 覆盖高频远程操作。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="grid gap-2">
              {Object.entries(ROLE_META).map(([role, meta]) => (
                <div key={role} className="flex items-center justify-between rounded-md border px-3 py-2">
                  <span className="font-mono">{meta.label}</span>
                  <span className="text-muted-foreground">{meta.desc}</span>
                </div>
              ))}
            </div>
            <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 text-xs leading-5">
              {HELP_PREVIEW}
            </pre>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Bell className="h-4 w-4" /> 授权用户
          </CardTitle>
          <CardDescription>
            未授权用户默认无响应。授权用户发 /start 后会记录 last_chat_id，用于通知和测试发送。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 rounded-md border p-3 md:grid-cols-[180px_minmax(0,1fr)_150px_120px]">
            <div className="space-y-1.5">
              <Label>Telegram 用户 ID</Label>
              <Input
                inputMode="numeric"
                placeholder="123456789"
                value={newUser.tg_user_id || ""}
                onChange={(e) =>
                  setNewUser((v) => ({ ...v, tg_user_id: Number(e.target.value) || 0 }))
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label>备注名</Label>
              <Input
                placeholder="例如：我 / 运维同事"
                value={newUser.display_name ?? ""}
                onChange={(e) => setNewUser((v) => ({ ...v, display_name: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label>角色</Label>
              <Select
                value={newUser.role}
                onChange={(e) =>
                  setNewUser((v) => ({ ...v, role: e.target.value as AccountBotRole }))
                }
              >
                {Object.keys(ROLE_META).map((role) => (
                  <option key={role} value={role}>
                    {role}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex items-end">
              <Button
                className="w-full"
                onClick={() => addUserMut.mutate()}
                disabled={addUserMut.isPending}
              >
                <UserPlus className="mr-1 h-4 w-4" />
                添加
              </Button>
            </div>
          </div>

          <Table className="table-fixed">
            <colgroup>
              <col className="w-[22%]" />
              <col className="w-[18%]" />
              <col className="w-[16%]" />
              <col className="w-[12%]" />
              <col className="w-[14%]" />
              <col className="w-[18%]" />
            </colgroup>
            <TableHeader>
              <TableRow>
                <TableHead>用户</TableHead>
                <TableHead>备注</TableHead>
                <TableHead>角色</TableHead>
                <TableHead className="text-center">通知</TableHead>
                <TableHead className="text-center">启用</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.id}>
                  <TableCell>
                    <div className="font-mono">{u.tg_user_id}</div>
                    <div className="text-xs text-muted-foreground">
                      chat: {u.last_chat_id ?? "未记录"}
                    </div>
                  </TableCell>
                  <TableCell>
                    <Input
                      defaultValue={u.display_name ?? ""}
                      placeholder="无"
                      onBlur={(e) => {
                        if (e.target.value === (u.display_name ?? "")) return;
                        updateUserMut.mutate({
                          uid: u.id,
                          patch: { display_name: e.target.value || null },
                        });
                      }}
                    />
                  </TableCell>
                  <TableCell>
                    <Select
                      value={u.role}
                      onChange={(e) =>
                        updateUserMut.mutate({
                          uid: u.id,
                          patch: { role: e.target.value as AccountBotRole },
                        })
                      }
                    >
                      {Object.keys(ROLE_META).map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </Select>
                  </TableCell>
                  <TableCell className="text-center">
                    <Switch
                      checked={u.notify_enabled}
                      onCheckedChange={(v) =>
                        updateUserMut.mutate({ uid: u.id, patch: { notify_enabled: v } })
                      }
                    />
                  </TableCell>
                  <TableCell className="text-center">
                    <Switch
                      checked={u.enabled}
                      onCheckedChange={(v) =>
                        updateUserMut.mutate({ uid: u.id, patch: { enabled: v } })
                      }
                    />
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <span className="hidden text-xs text-muted-foreground xl:inline">
                        {formatDateTime(u.created_at)}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-destructive"
                        onClick={() => deleteUserMut.mutate(u.id)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {users.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="h-20 text-center text-muted-foreground">
                    还没有授权用户
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
