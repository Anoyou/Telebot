import { useCallback, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Download,
  Upload,
  CheckCircle2,
  XCircle,
  AlertCircle,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select } from "@/components/ui/select";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api } from "@/lib/api";
import { getErrMsg } from "@/lib/api";
import { dryRunConfigBundle, exportConfigBundle, listAccounts } from "@/api/accounts";
import type { ConfigBundleDryRunResponse } from "@/api/types";

interface CategoryDef {
  key: string;
  label: string;
  desc: string;
  sensitive?: string[];
}

const CATEGORIES: CategoryDef[] = [
  { key: "system_settings", label: "系统设置", desc: "命令前缀等全局配置" },
  { key: "command_templates", label: "自定义命令模板", desc: "所有回复/转发/AI 命令模板" },
  { key: "account_commands", label: "账号-命令绑定", desc: "每个账号启用了哪些命令" },
  { key: "llm_providers", label: "LLM Provider", desc: "AI 模型提供商配置", sensitive: ["api_key"] },
  { key: "forward_rules", label: "消息转发规则", desc: "自动转发配置" },
  { key: "auto_reply_rules", label: "自动回复规则", desc: "自动回复配置" },
  { key: "rate_limit_templates", label: "风控模板", desc: "限速规则模板" },
  { key: "rate_limit_rules", label: "风控规则", desc: "账号级限速配置" },
  { key: "feature_config", label: "插件功能配置", desc: "各账号的插件开关和配置" },
  { key: "account_settings", label: "账号设置", desc: "拟人化、标签等（不含登录信息）", sensitive: ["session", "api_id", "api_hash", "phone"] },
  { key: "ignored_peers", label: "忽略列表", desc: "自动回复/转发忽略的 peer" },
  { key: "notify_bots", label: "通知 Bot", desc: "通知机器人配置", sensitive: ["bot_token"] },
];

export function ConfigBackup() {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [includeSensitive, setIncludeSensitive] = useState(false);
  const [importResult, setImportResult] = useState<{
    imported: number;
    skipped: number;
    warnings: string[];
  } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const bundleFileRef = useRef<HTMLInputElement>(null);
  const [bundleSourceAid, setBundleSourceAid] = useState("");
  const [bundleTargetAid, setBundleTargetAid] = useState("");
  const [bundleResult, setBundleResult] = useState<ConfigBundleDryRunResponse | null>(null);

  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  const toggleCategory = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const selectAll = () => {
    if (selected.size === CATEGORIES.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(CATEGORIES.map((c) => c.key)));
    }
  };

  const exportMut = useMutation({
    mutationFn: async () => {
      const res = await api.post("/api/system/export-config", {
        categories: Array.from(selected),
        includeSensitive,
      }, { responseType: "blob" });
      // 从 Content-Disposition 提取文件名
      const disposition = res.headers["content-disposition"] || "";
      const match = disposition.match(/filename="?(.+?)"?(?:;|$)/);
      const filename = match ? match[1] : `telebot-config-${new Date().toISOString().slice(0, 10)}.json`;
      // 触发下载
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
    onSuccess: () => toast.success("配置已导出"),
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const importMut = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      const { data } = await api.post("/api/system/import-config", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return data as { imported: number; skipped: number; warnings: string[] };
    },
    onSuccess: (data) => {
      setImportResult(data);
      toast.success(`导入完成：${data.imported} 条成功，${data.skipped} 条跳过`);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const exportBundleMut = useMutation({
    mutationFn: async () => {
      const res = await exportConfigBundle(Number(bundleSourceAid));
      const disposition = res.headers["content-disposition"] || "";
      const match = disposition.match(/filename="?(.+?)"?(?:;|$)/);
      const filename = match ? match[1] : `telebot-config-bundle-${bundleSourceAid}.json`;
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
    onSuccess: () => toast.success("Config Bundle 已导出"),
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const dryRunBundleMut = useMutation({
    mutationFn: async (file: File) => dryRunConfigBundle(Number(bundleTargetAid), file),
    onSuccess: (data) => {
      setBundleResult(data);
      toast.success(
        `dry-run 完成：新增 ${data.counts.add}，跳过 ${data.counts.skip}，冲突 ${data.counts.conflict}`,
      );
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      setImportResult(null);
      importMut.mutate(file);
      // 清空 file input 以支持重复选择同一文件
      e.target.value = "";
    },
    [importMut],
  );

  const handleBundleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      setBundleResult(null);
      dryRunBundleMut.mutate(file);
      e.target.value = "";
    },
    [dryRunBundleMut],
  );

  return (
    <>
      <Card>
      <CardHeader>
        <CardTitle className="text-base">备份与恢复</CardTitle>
        <CardDescription>导出或导入系统配置（可选是否包含敏感数据）</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* 导出区域 */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label className="text-sm font-medium">选择导出类别</Label>
            <Button variant="ghost" size="sm" onClick={selectAll} className="text-xs">
              {selected.size === CATEGORIES.length ? "取消全选" : "全选"}
            </Button>
          </div>

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {CATEGORIES.map((cat) => (
              <label
                key={cat.key}
                className="flex items-start gap-2 rounded-md border px-3 py-2 cursor-pointer hover:bg-muted/50 transition-colors"
              >
                <input
                  type="checkbox"
                  checked={selected.has(cat.key)}
                  onChange={() => toggleCategory(cat.key)}
                  className="mt-0.5"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-medium">{cat.label}</span>
                    {cat.sensitive && (
                      <span className="rounded bg-amber-50 px-1 text-[10px] text-amber-600 dark:bg-amber-950/40 dark:text-amber-300">
                        含敏感数据
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">{cat.desc}</p>
                </div>
              </label>
            ))}
          </div>

          {/* 敏感数据开关 */}
          <div className="flex items-center justify-between rounded-md border px-3 py-2">
            <div>
              <Label className="text-sm">包含敏感数据</Label>
              <p className="text-xs text-muted-foreground">
                session、api_key、token、密码等加密字段
              </p>
            </div>
            <Switch
              checked={includeSensitive}
              onCheckedChange={setIncludeSensitive}
            />
          </div>

          {includeSensitive && (
            <div className="flex items-start gap-2 rounded-md border px-3 py-2 text-xs alert-warning">
              <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
              <span>导出后请妥善保管文件。导入端需使用相同的 MASTER_KEY 才能解密敏感数据。</span>
            </div>
          )}

          <Button
            onClick={() => exportMut.mutate()}
            disabled={selected.size === 0 || exportMut.isPending}
            className="gap-1.5"
          >
            <Download className="h-4 w-4" />
            {exportMut.isPending ? "导出中..." : `导出配置（${selected.size} 项）`}
          </Button>
        </div>

        {/* 分隔线 */}
        <div className="border-t" />

        {/* 导入区域 */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">导入配置</Label>
          <p className="text-xs text-muted-foreground">
            上传之前导出的 JSON 文件。同名/同 ID 的配置项将被跳过。
          </p>

          <input
            ref={fileRef}
            type="file"
            accept=".json"
            onChange={handleFileChange}
            className="hidden"
          />

          <Button
            variant="outline"
            onClick={() => fileRef.current?.click()}
            disabled={importMut.isPending}
            className="gap-1.5"
          >
            <Upload className="h-4 w-4" />
            {importMut.isPending ? "导入中..." : "选择文件导入"}
          </Button>

          {/* 导入结果 */}
          {importResult && (
            <div className="rounded-md border px-3 py-2 space-y-2">
              <div className="flex items-center gap-4 text-sm">
                <span className="flex items-center gap-1 text-emerald-600 dark:text-emerald-300">
                  <CheckCircle2 className="h-4 w-4" />
                  成功 {importResult.imported}
                </span>
                <span className="flex items-center gap-1 text-muted-foreground">
                  <XCircle className="h-4 w-4" />
                  跳过 {importResult.skipped}
                </span>
              </div>
              {importResult.warnings.length > 0 && (
                <div className="space-y-1 text-xs text-amber-600 dark:text-amber-300">
                  {importResult.warnings.slice(0, 5).map((w, i) => (
                    <p key={i}>{w}</p>
                  ))}
                  {importResult.warnings.length > 5 && (
                    <p>... 还有 {importResult.warnings.length - 5} 条警告</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </CardContent>
      </Card>

      <Card>
      <CardHeader>
        <CardTitle className="text-base">Config Bundle</CardTitle>
        <CardDescription>账号级配置包导出与 dry-run（仅预览，不写入）</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-3">
          <div className="space-y-1.5 max-w-md">
            <Label>导出源账号</Label>
            <Select value={bundleSourceAid} onChange={(e) => setBundleSourceAid(e.target.value)}>
              <option value="">-- 选择账号 --</option>
              {accountsQ.data?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name || a.phone}
                </option>
              ))}
            </Select>
          </div>
          <Button
            onClick={() => exportBundleMut.mutate()}
            disabled={!bundleSourceAid || exportBundleMut.isPending}
            className="gap-1.5"
          >
            <Download className="h-4 w-4" />
            {exportBundleMut.isPending ? "导出中..." : "导出 Config Bundle"}
          </Button>
        </div>

        <div className="border-t" />

        <div className="space-y-3">
          <div className="space-y-1.5 max-w-md">
            <Label>dry-run 目标账号</Label>
            <Select value={bundleTargetAid} onChange={(e) => setBundleTargetAid(e.target.value)}>
              <option value="">-- 选择账号 --</option>
              {accountsQ.data?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name || a.phone}
                </option>
              ))}
            </Select>
          </div>
          <input
            ref={bundleFileRef}
            type="file"
            accept=".json,application/json"
            onChange={handleBundleFileChange}
            className="hidden"
          />
          <Button
            variant="outline"
            onClick={() => bundleFileRef.current?.click()}
            disabled={!bundleTargetAid || dryRunBundleMut.isPending}
            className="gap-1.5"
          >
            <Upload className="h-4 w-4" />
            {dryRunBundleMut.isPending ? "分析中..." : "上传 bundle 做 dry-run"}
          </Button>

          {bundleResult && (
            <div className="space-y-3 rounded-md border px-3 py-3">
              <div className="flex flex-wrap gap-3 text-sm">
                <span className="flex items-center gap-1 text-emerald-600 dark:text-emerald-300">
                  <CheckCircle2 className="h-4 w-4" />
                  新增 {bundleResult.counts.add}
                </span>
                <span className="flex items-center gap-1 text-muted-foreground">
                  <XCircle className="h-4 w-4" />
                  跳过 {bundleResult.counts.skip}
                </span>
                <span className="flex items-center gap-1 text-red-600 dark:text-red-300">
                  <AlertCircle className="h-4 w-4" />
                  冲突 {bundleResult.counts.conflict}
                </span>
              </div>
              <div className="text-xs text-muted-foreground">
                bundle 大小 {Math.round(bundleResult.size_bytes / 1024)} KB
              </div>
              {bundleResult.warnings.length > 0 && (
                <div className="space-y-1 text-xs text-amber-600 dark:text-amber-300">
                  {bundleResult.warnings.map((w, i) => (
                    <p key={i}>{w}</p>
                  ))}
                </div>
              )}
              <div className="max-h-72 space-y-1 overflow-auto text-xs">
                {bundleResult.items.slice(0, 40).map((item, i) => (
                  <div
                    key={`${item.entity}-${item.key}-${i}`}
                    className={[
                      "rounded border px-2 py-1",
                      item.action === "conflict"
                        ? "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
                        : item.action === "add"
                          ? "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
                          : "border-border text-muted-foreground",
                    ].join(" ")}
                  >
                    <span className="font-medium">{item.entity}</span>
                    {" · "}
                    <span>{item.key}</span>
                    {" · "}
                    <span>{item.action}</span>
                    {item.fields.length > 0 && (
                      <span className="ml-2">[{item.fields.join(", ")}]</span>
                    )}
                    {item.note && <span className="ml-2">{item.note}</span>}
                  </div>
                ))}
              </div>
              {bundleResult.items.length > 40 && (
                <p className="text-xs text-muted-foreground">
                  ... 还有 {bundleResult.items.length - 40} 条结果
                </p>
              )}
            </div>
          )}
        </div>
      </CardContent>
      </Card>
    </>
  );
}
