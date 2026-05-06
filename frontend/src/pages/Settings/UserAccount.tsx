// 系统设置 → 当前用户账号管理：修改密码 + (可选) 禁用 TOTP
//
// 不提供"用户列表"——本系统是单租户的超管模型，只有一个 web 用户；
// 真正需要换人时走数据库手动改 username + 密码即可。
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { KeyRound, ShieldCheck } from "lucide-react";

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
import { fetchMe } from "@/lib/auth";
import { api, getErrMsg } from "@/lib/api";

export function UserAccount() {
  const qc = useQueryClient();
  const meQ = useQuery({ queryKey: ["auth", "me"], queryFn: fetchMe });

  // ── 修改密码 ────────────────────────────────────────────────
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [newPwd2, setNewPwd2] = useState("");

  const changeMut = useMutation({
    mutationFn: async () => {
      await api.post("/api/auth/change-password", {
        old_password: oldPwd,
        new_password: newPwd,
      });
    },
    onSuccess: () => {
      // 后端已清 cookie；提示后跳登录页
      toast.success("密码已修改，请用新密码重新登录");
      setOldPwd("");
      setNewPwd("");
      setNewPwd2("");
      qc.clear();
      // 用 hard reload 避免 React Query 还在用旧 token 再发请求
      setTimeout(() => {
        window.location.href = "/login";
      }, 800);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const handleChange = () => {
    if (!oldPwd || !newPwd || !newPwd2) {
      toast.error("三项都要填");
      return;
    }
    if (newPwd.length < 8) {
      toast.error("新密码至少 8 位");
      return;
    }
    if (newPwd !== newPwd2) {
      toast.error("两次输入的新密码不一致");
      return;
    }
    if (oldPwd === newPwd) {
      toast.error("新密码不能与旧密码相同");
      return;
    }
    changeMut.mutate();
  };

  // ── 禁用 动态验证码（TOTP） ──────────────────────────────────────────────
  const [totpCode, setTotpCode] = useState("");
  const disableTotpMut = useMutation({
    mutationFn: async () => {
      await api.post("/api/auth/totp/disable", { code: totpCode });
    },
    onSuccess: () => {
      toast.success("已禁用动态验证码（TOTP）");
      setTotpCode("");
      qc.invalidateQueries({ queryKey: ["auth", "me"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <KeyRound className="h-4 w-4" /> 当前账号
        </CardTitle>
        <CardDescription>
          {meQ.data ? (
            <>
              当前已登录：<span className="font-mono">{meQ.data.username}</span>
              {meQ.data.has_totp ? (
                <span className="ml-2 inline-flex items-center gap-1 text-emerald-700">
                  <ShieldCheck className="h-3.5 w-3.5" /> 动态验证码（TOTP）已启用
                </span>
              ) : (
                <span className="ml-2 text-amber-700">动态验证码（TOTP）未启用</span>
              )}
            </>
          ) : (
            "加载中…"
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* 修改密码 */}
        <div className="space-y-3 max-w-md">
          <h3 className="text-sm font-medium">修改密码</h3>
          <div className="space-y-2">
            <Label htmlFor="oldpwd">当前密码</Label>
            <Input
              id="oldpwd"
              type="password"
              autoComplete="current-password"
              value={oldPwd}
              onChange={(e) => setOldPwd(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="newpwd">新密码（≥ 8 位）</Label>
            <Input
              id="newpwd"
              type="password"
              autoComplete="new-password"
              value={newPwd}
              onChange={(e) => setNewPwd(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="newpwd2">确认新密码</Label>
            <Input
              id="newpwd2"
              type="password"
              autoComplete="new-password"
              value={newPwd2}
              onChange={(e) => setNewPwd2(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleChange();
              }}
            />
          </div>
          <Button onClick={handleChange} disabled={changeMut.isPending}>
            修改密码
          </Button>
          <p className="text-xs text-muted-foreground">
            修改成功后会强制下线，请用新密码重新登录
          </p>
        </div>

        {/* 禁用 动态验证码（TOTP）（仅当前已启用时显示） */}
        {meQ.data?.has_totp ? (
          <div className="space-y-3 max-w-md border-t pt-4">
            <h3 className="text-sm font-medium">禁用动态验证码</h3>
            <p className="text-xs text-muted-foreground">
              输入当前 6 位 动态验证码（TOTP） 码以禁用 2FA。禁用后下次登录将不再要求二次验证。
            </p>
            <div className="flex gap-2 items-end">
              <div className="flex-1 space-y-2">
                <Label htmlFor="totpcode">当前 动态验证码（TOTP） </Label>
                <Input
                  id="totpcode"
                  inputMode="numeric"
                  maxLength={8}
                  placeholder="6 位数字"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ""))}
                />
              </div>
              <Button
                variant="outline"
                onClick={() => {
                  if (totpCode.length < 6) {
                    toast.error("TOTP 码至少 6 位");
                    return;
                  }
                  if (!confirm("确认禁用 TOTP？账号安全等级会降低")) return;
                  disableTotpMut.mutate();
                }}
                disabled={disableTotpMut.isPending}
              >
                禁用 动态验证码（TOTP）
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
