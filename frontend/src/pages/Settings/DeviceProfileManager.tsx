// 登录设备管理（第 20 轮前叫「设备标识模板 / 设备伪装库」，API 与字段名不变）：列表 + 新建 + 内联编辑 + 设为默认 + 删除。
// 在 Settings 页里以一个 Card 形式嵌入。
//
// 为什么需要：Telegram 会把 `device_model` / `system_version` / `app_version` 显示在设备列表里，
// 这些值通过 Telethon 的 init_connection 注册。每条 profile 可被账号引用，不引用就用 is_default。
//
// 重要：profile 的修改不会影响**已有 session**。TG 把设备名绑在 auth_key 上。
// 改了 profile 还得让账号重新登录走 wizard，TG 才会看到新值。
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Pencil, Plus, Star, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Spinner } from "@/components/ui/misc";
import { SectionHeader, SignalPill } from "@/components/ui/status";
import {
  createDeviceProfile,
  deleteDeviceProfile,
  listDeviceProfiles,
  patchDeviceProfile,
  setDefaultDeviceProfile,
} from "@/api/device-profiles";
import type {
  DeviceProfileCreate,
  DeviceProfileOut,
} from "@/api/types";
import { getErrMsg } from "@/lib/api";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/feedback/EmptyState";

// 默认值方便快速创建：直接照抄 macOS Telegram
const DEFAULT_FORM: DeviceProfileCreate = {
  name: "",
  device_model: "MacBook Pro",
  system_version: "macOS 14.5",
  app_version: "Telegram macOS 11.5",
  lang_code: "zh",
  system_lang_code: "zh-Hans",
  is_default: false,
};

export function DeviceProfileManager() {
  const qc = useQueryClient();
  const profilesQ = useQuery({
    queryKey: ["device-profiles"],
    queryFn: listDeviceProfiles,
  });

  // 新建表单状态
  const [form, setForm] = useState<DeviceProfileCreate>(DEFAULT_FORM);
  const [showCreate, setShowCreate] = useState(false);

  const createMut = useMutation({
    mutationFn: () => createDeviceProfile(form),
    onSuccess: () => {
      toast.success("已创建");
      setForm(DEFAULT_FORM);
      setShowCreate(false);
      qc.invalidateQueries({ queryKey: ["device-profiles"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const setDefaultMut = useMutation({
    mutationFn: setDefaultDeviceProfile,
    onSuccess: () => {
      toast.success("已设为默认");
      qc.invalidateQueries({ queryKey: ["device-profiles"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteMut = useMutation({
    mutationFn: deleteDeviceProfile,
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["device-profiles"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <SectionHeader
              title="登录设备"
              description="登录 Telegram 时上报的设备信息，会显示在 Telegram「设置 › 设备」列表里，例如 iPhone 15 Pro · iOS 18。修改只对之后新登录的账号生效，已登录的账号要重新登录才会显示新值。"
              meta={
                <SignalPill
                  tone={(profilesQ.data?.length ?? 0) > 0 ? "primary" : "neutral"}
                  label="数量"
                  value={profilesQ.data?.length ?? 0}
                />
              }
            />
          </div>
          {!showCreate ? (
            <Button size="sm" onClick={() => setShowCreate(true)}>
              <Plus className="mr-1 h-4 w-4" /> 新增
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setShowCreate(false)}>
              <X className="mr-1 h-4 w-4" /> 取消
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 新建表单 */}
        {showCreate ? (
          <div className="rounded-lg border bg-muted/30 p-4">
            <ProfileForm
              value={form}
              onChange={setForm}
              showName
              showIsDefault
            />
            <div className="mt-3 flex justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setForm(DEFAULT_FORM);
                  setShowCreate(false);
                }}
              >
                取消
              </Button>
              <Button
                size="sm"
                onClick={() => createMut.mutate()}
                disabled={!form.name.trim() || createMut.isPending}
              >
                <Plus className="mr-1 h-4 w-4" />
                创建
              </Button>
            </div>
          </div>
        ) : null}

        {/* 列表 */}
        {profilesQ.isLoading ? (
          <div className="flex h-20 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : profilesQ.data && profilesQ.data.length > 0 ? (
          <ul className="space-y-2">
            {profilesQ.data.map((p) => (
              <ProfileRow
                key={p.id}
                profile={p}
                onSetDefault={() => setDefaultMut.mutate(p.id)}
                onDelete={async () => {
                  if (await confirmDialog({ title: `删除登录设备「${p.name}」？`, description: "使用它的账号会改用默认登录设备。", confirmLabel: "删除", tone: "danger" }))
                    deleteMut.mutate(p.id);
                }}
              />
            ))}
          </ul>
        ) : (
          <EmptyState title="还没有登录设备" description="数据库迁移会预置 macOS / iPhone / Windows 等几条，也可以在上方新增。" />
        )}
      </CardContent>
    </Card>
  );
}

// ── 单行 ──────────────────────────────────────────────────────────

function ProfileRow({
  profile,
  onSetDefault,
  onDelete,
}: {
  profile: DeviceProfileOut;
  onSetDefault: () => void;
  onDelete: () => void;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<DeviceProfileCreate>({
    name: profile.name,
    device_model: profile.device_model,
    system_version: profile.system_version,
    app_version: profile.app_version,
    lang_code: profile.lang_code,
    system_lang_code: profile.system_lang_code,
  });

  const patchMut = useMutation({
    mutationFn: () =>
      patchDeviceProfile(profile.id, {
        name: draft.name !== profile.name ? draft.name : undefined,
        device_model:
          draft.device_model !== profile.device_model ? draft.device_model : undefined,
        system_version:
          draft.system_version !== profile.system_version
            ? draft.system_version
            : undefined,
        app_version:
          draft.app_version !== profile.app_version ? draft.app_version : undefined,
        lang_code:
          draft.lang_code !== profile.lang_code ? draft.lang_code : undefined,
        system_lang_code:
          draft.system_lang_code !== profile.system_lang_code
            ? draft.system_lang_code
            : undefined,
      }),
    onSuccess: () => {
      toast.success("已保存");
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["device-profiles"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <li
      className={cn(
        "rounded-lg border p-3",
        profile.is_default && "border-primary/40 bg-primary/5",
      )}
    >
      {/* 头部：名称 + 默认徽章 + 操作 */}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{profile.name}</span>
            {profile.is_default ? (
              <span className="inline-flex items-center gap-0.5 rounded-sm bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                <Star className="h-2.5 w-2.5" /> 默认
              </span>
            ) : null}
          </div>
          {/* 摘要（非编辑态） */}
          {!editing ? (
            <div className="mt-1 space-y-0.5 text-xs text-muted-foreground">
              <div className="font-mono">
                {profile.device_model} · {profile.system_version} · {profile.app_version}
              </div>
              <div className="text-[11px]">
                语言代码：{profile.lang_code} / {profile.system_lang_code}
              </div>
            </div>
          ) : null}
        </div>

        {/* 操作 */}
        <div className="flex shrink-0 items-center gap-1">
          {!editing ? (
            <>
              {!profile.is_default ? (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-8 px-2"
                  onClick={onSetDefault}
                  title="设为默认"
                >
                  <Star className="h-3.5 w-3.5" />
                </Button>
              ) : null}
              <Button
                size="sm"
                variant="ghost"
                className="h-8 px-2"
                onClick={() => setEditing(true)}
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 px-2 text-destructive hover:text-destructive"
                onClick={onDelete}
                disabled={profile.is_default}
                title={profile.is_default ? "默认登录设备不可删除" : "删除"}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </>
          ) : (
            <>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 px-2"
                onClick={() => {
                  setDraft({
                    name: profile.name,
                    device_model: profile.device_model,
                    system_version: profile.system_version,
                    app_version: profile.app_version,
                    lang_code: profile.lang_code,
                    system_lang_code: profile.system_lang_code,
                  });
                  setEditing(false);
                }}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="sm"
                className="h-8 px-2"
                onClick={() => patchMut.mutate()}
                disabled={patchMut.isPending}
              >
                <Check className="h-3.5 w-3.5" />
              </Button>
            </>
          )}
        </div>
      </div>

      {/* 编辑态字段 */}
      {editing ? (
        <div className="mt-3 border-t pt-3">
          <ProfileForm value={draft} onChange={setDraft} showName />
        </div>
      ) : null}
    </li>
  );
}

// ── 表单（创建 / 编辑共用） ─────────────────────────────────────────

function ProfileForm({
  value,
  onChange,
  showName,
  showIsDefault,
}: {
  value: DeviceProfileCreate;
  onChange: (v: DeviceProfileCreate) => void;
  showName?: boolean;
  showIsDefault?: boolean;
}) {
  const set = <K extends keyof DeviceProfileCreate>(
    k: K,
    v: DeviceProfileCreate[K],
  ) => onChange({ ...value, [k]: v });

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {showName ? (
        <Field label="名称" hint="例如：「我的 Mac」「老张的 iPhone」">
          <Input
            value={value.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="登录设备名称"
          />
        </Field>
      ) : null}
      <Field label="机型" hint="device_model · Telegram 设备列表里的主标题，例如 iPhone 15 Pro">
        <Input
          value={value.device_model}
          onChange={(e) => set("device_model", e.target.value)}
          placeholder="MacBook Pro"
        />
      </Field>
      <Field label="系统版本" hint="system_version · 副标题前半段，例如 iOS 18">
        <Input
          value={value.system_version}
          onChange={(e) => set("system_version", e.target.value)}
          placeholder="macOS 14.5"
        />
      </Field>
      <Field label="App 版本" hint="app_version · 副标题后半段，例如 Telegram iOS 11.5">
        <Input
          value={value.app_version}
          onChange={(e) => set("app_version", e.target.value)}
          placeholder="Telegram macOS 11.5"
        />
      </Field>
      <Field label="语言代码" hint="lang_code · App 界面语言，例如 zh">
        <Input
          value={value.lang_code ?? "zh"}
          onChange={(e) => set("lang_code", e.target.value)}
          placeholder="zh"
        />
      </Field>
      <Field label="系统语言代码" hint="system_lang_code · 系统语言，例如 zh-Hans">
        <Input
          value={value.system_lang_code ?? "zh-Hans"}
          onChange={(e) => set("system_lang_code", e.target.value)}
          placeholder="zh-Hans"
        />
      </Field>
      {showIsDefault ? (
        <Field label="设为默认" hint="打开后其它登录设备自动取消默认">
          <div className="flex h-10 items-center">
            <Switch
              checked={value.is_default ?? false}
              onCheckedChange={(v) => set("is_default", v)}
            />
          </div>
        </Field>
      ) : null}
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {children}
      {hint ? (
        <p className="text-[11px] text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}
