// 插件中心：合并原「功能矩阵」+「插件管理」+「开发指南」三处入口
//
// Tab 1：功能矩阵 — 账号 × 功能 启停状态总览
// Tab 2：已加载插件 — 插件列表 + enable/disable + uninstall
// Tab 3：开发指南 — react-markdown 渲染 docs/PLUGIN-DEV-GUIDE.md
//
// 之前 /matrix 和 /extensions 两个独立菜单项被砍，访问会 redirect 到这里（App.tsx）。
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BookOpen, Check, Layers, Puzzle, X } from "lucide-react";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github.css";
import devGuideMd from "../../../docs/PLUGIN-DEV-GUIDE.md?raw";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/misc";
import { getFeatureMatrix } from "@/api/features";
import { toggleAccountFeature, cloneConfig } from "@/api/accounts";
import {
  disableInstall,
  enableInstall,
  listInstalledPackages,
  uninstallPlugin,
} from "@/api/plugins";
import { getErrMsg } from "@/lib/api";
import type { FeatureMatrixResponse, FeatureState } from "@/api/types";
import { cn, formatDateTime } from "@/lib/utils";

const PLUGINS_QK = ["plugins", "installed-packages"] as const;

export function Extensions() {
  const [tab, setTab] = useState<"matrix" | "plugins" | "guide">("matrix");

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">插件中心</h1>
        <p className="text-sm text-muted-foreground">
          功能矩阵 + 插件管理 + 开发指南。给开发者 / 想扩展功能的人看
        </p>
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList>
          <TabsTrigger value="matrix" className="gap-1.5">
            <Layers className="h-4 w-4" /> 功能矩阵
          </TabsTrigger>
          <TabsTrigger value="plugins" className="gap-1.5">
            <Puzzle className="h-4 w-4" /> 已加载插件
          </TabsTrigger>
          <TabsTrigger value="guide" className="gap-1.5">
            <BookOpen className="h-4 w-4" /> 开发指南
          </TabsTrigger>
        </TabsList>

        <TabsContent value="matrix">
          <FeatureMatrixTab />
        </TabsContent>
        <TabsContent value="plugins">
          <PluginsTab />
        </TabsContent>
        <TabsContent value="guide">
          <DevGuideTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── Tab 1：功能矩阵 ────────────────────────────────────────────────
interface CellInfo {
  aid: number;
  aname: string;
  fkey: string;
  fname: string;
  state: FeatureState;
}

function StateIcon({ state }: { state: FeatureState }) {
  if (state === "active")
    return <Check className="mx-auto h-5 w-5 text-emerald-500" />;
  if (state === "failed")
    return <AlertTriangle className="mx-auto h-5 w-5 text-destructive" />;
  return <X className="mx-auto h-5 w-5 text-muted-foreground" />;
}

function FeatureMatrixTab() {
  const nav = useNavigate();
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["matrix"],
    queryFn: getFeatureMatrix,
  });

  const [openCell, setOpenCell] = useState<CellInfo | null>(null);
  const [cloneFromAid, setCloneFromAid] = useState<string>("");

  const toggleMut = useMutation({
    mutationFn: async (vars: { aid: number; key: string; enabled: boolean }) =>
      toggleAccountFeature(vars.aid, vars.key, vars.enabled),
    onSuccess: (_d, vars) => {
      toast.success(vars.enabled ? "已启用（worker 激活中…）" : "已禁用");
      // 乐观更新已在 onMutate 中完成；这里延迟刷新让 worker 有时间将 state 改为 active
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["matrix"] });
        qc.invalidateQueries({ queryKey: ["accounts"] });
      }, 1500);
    },
    onError: (err, vars, ctx) => {
      // 回滚乐观更新
      if (ctx?.snapshot) qc.setQueryData(["matrix"], ctx.snapshot);
      toast.error(getErrMsg(err));
    },
    onMutate: async (vars) => {
      // 取消正在进行的 matrix 查询，避免覆盖乐观更新
      await qc.cancelQueries({ queryKey: ["matrix"] });
      const snapshot = qc.getQueryData<FeatureMatrixResponse>(["matrix"]);
      if (snapshot) {
        qc.setQueryData<FeatureMatrixResponse>(["matrix"], {
          ...snapshot,
          accounts: snapshot.accounts.map((row) =>
            row.id === vars.aid
              ? {
                  ...row,
                  features: {
                    ...row.features,
                    [vars.key]: vars.enabled ? "active" : "disabled",
                  },
                }
              : row,
          ),
        });
      }
      return { snapshot };
    },
  });

  const cloneMut = useMutation({
    mutationFn: async (vars: { toAid: number; fromAid: number; key: string }) =>
      cloneConfig(vars.toAid, vars.fromAid, [vars.key]),
    onSuccess: () => {
      toast.success("已克隆规则");
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">账号 × 功能 矩阵</CardTitle>
        <CardDescription>
          ✓ active · ⚠ failed · ✗ disabled — 点击格子可启停 / 跳配置 / 克隆其他账号规则
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex h-24 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : data && data.accounts.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>账号 \ 功能</TableHead>
                {data.features.map((f) => (
                  <TableHead key={f.key} className="text-center">
                    {f.display_name}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.accounts.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-medium">{row.name}</TableCell>
                  {data.features.map((f) => {
                    const state = (row.features[f.key] ?? "disabled") as FeatureState;
                    return (
                      <TableCell
                        key={f.key}
                        className={cn(
                          "cursor-pointer text-center hover:bg-accent/50",
                        )}
                        onClick={() =>
                          setOpenCell({
                            aid: row.id,
                            aname: row.name,
                            fkey: f.key,
                            fname: f.display_name,
                            state,
                          })
                        }
                      >
                        <StateIcon state={state} />
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="py-8 text-center text-sm text-muted-foreground">
            尚未绑定账号
          </p>
        )}
      </CardContent>

      <Dialog open={!!openCell} onOpenChange={(v) => !v && setOpenCell(null)}>
        <DialogContent>
          {openCell && (
            <>
              <DialogHeader>
                <DialogTitle>
                  {openCell.aname} · {openCell.fname}
                </DialogTitle>
                <DialogDescription>当前状态：{openCell.state}</DialogDescription>
              </DialogHeader>

              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {openCell.state !== "active" ? (
                    <Button
                      onClick={() => {
                        toggleMut.mutate({
                          aid: openCell.aid,
                          key: openCell.fkey,
                          enabled: true,
                        });
                        setOpenCell(null);
                      }}
                    >
                      启用
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      onClick={() => {
                        toggleMut.mutate({
                          aid: openCell.aid,
                          key: openCell.fkey,
                          enabled: false,
                        });
                        setOpenCell(null);
                      }}
                    >
                      禁用
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    onClick={() => {
                      const aid = openCell.aid;
                      const key = openCell.fkey;
                      setOpenCell(null);
                      nav(`/accounts/${aid}/features/${key}`);
                    }}
                  >
                    打开配置页
                  </Button>
                </div>

                <div className="space-y-1.5 border-t pt-3">
                  <p className="text-xs text-muted-foreground">
                    从其他账号复制规则
                  </p>
                  <div className="flex gap-2">
                    <Select
                      value={cloneFromAid}
                      onChange={(e) => setCloneFromAid(e.target.value)}
                    >
                      <option value="">-- 选择来源账号 --</option>
                      {data?.accounts
                        .filter((a) => a.id !== openCell.aid)
                        .map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name}
                          </option>
                        ))}
                    </Select>
                    <Button
                      disabled={!cloneFromAid}
                      onClick={() => {
                        cloneMut.mutate({
                          toAid: openCell.aid,
                          fromAid: Number(cloneFromAid),
                          key: openCell.fkey,
                        });
                        setOpenCell(null);
                        setCloneFromAid("");
                      }}
                    >
                      克隆
                    </Button>
                  </div>
                </div>
              </div>

              <DialogFooter>
                <Button variant="ghost" onClick={() => setOpenCell(null)}>
                  关闭
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

// ── Tab 2：已加载插件 ──────────────────────────────────────────────
function PluginsTab() {
  const qc = useQueryClient();

  // Builtin 插件列表（来自 feature-matrix 的 features 字段）
  const builtinQ = useQuery({
    queryKey: ["matrix"],
    queryFn: getFeatureMatrix,
    select: (data) => data.features.filter((f) => f.is_builtin),
  });

  // 第三方插件列表（来自 plugin_install 表）
  const thirdPartyQ = useQuery({ queryKey: PLUGINS_QK, queryFn: listInstalledPackages });

  const enableMut = useMutation({
    mutationFn: (key: string) => enableInstall(key),
    onSuccess: (row) => {
      toast.success(`已启用 ${row.key}`);
      qc.invalidateQueries({ queryKey: PLUGINS_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const disableMut = useMutation({
    mutationFn: (key: string) => disableInstall(key),
    onSuccess: (row) => {
      toast.success(`已禁用 ${row.key}`);
      qc.invalidateQueries({ queryKey: PLUGINS_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const uninstallMut = useMutation({
    mutationFn: (key: string) => uninstallPlugin(key),
    onSuccess: (_void, key) => {
      toast.success(`已卸载 ${key}`);
      qc.invalidateQueries({ queryKey: PLUGINS_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const isLoading = builtinQ.isLoading || thirdPartyQ.isLoading;
  const hasBuiltin = (builtinQ.data ?? []).length > 0;
  const hasThirdParty = (thirdPartyQ.data ?? []).length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">已加载插件</CardTitle>
        <CardDescription>
          builtin 插件 + 从 <code>data/plugins/installed/</code> 加载的第三方插件
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex h-24 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : !hasBuiltin && !hasThirdParty ? (
          <p className="rounded-md border border-dashed py-8 text-center text-xs text-muted-foreground">
            当前没有已安装插件
          </p>
        ) : (
          <>
            {/* Builtin 插件区 */}
            {hasBuiltin && (
              <>
                <div className="mb-2 text-xs font-medium text-muted-foreground">内置插件</div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Key</TableHead>
                      <TableHead>名称</TableHead>
                      <TableHead>类型</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {builtinQ.data!.map((f) => (
                      <TableRow key={f.key}>
                        <TableCell className="font-mono text-xs">{f.key}</TableCell>
                        <TableCell>{f.display_name}</TableCell>
                        <TableCell>
                          <Badge variant="secondary">builtin</Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}

            {/* 第三方插件区 */}
            {hasThirdParty && (
              <>
                {hasBuiltin && <div className="my-4 border-t" />}
                <div className="mb-2 text-xs font-medium text-muted-foreground">第三方插件</div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Key</TableHead>
                      <TableHead>版本</TableHead>
                      <TableHead>来源</TableHead>
                      <TableHead>状态</TableHead>
                      <TableHead>安装时间</TableHead>
                      <TableHead className="text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {thirdPartyQ.data!.map((row) => (
                      <TableRow key={row.key}>
                        <TableCell className="font-mono text-xs">{row.key}</TableCell>
                        <TableCell>{row.version}</TableCell>
                        <TableCell>
                          <Badge variant="secondary">{row.source}</Badge>
                        </TableCell>
                        <TableCell>
                          <Badge variant={row.enabled ? "default" : "outline"}>
                            {row.enabled ? "已启用" : "未启用"}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatDateTime(row.installed_at)}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-2">
                            {row.enabled ? (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => disableMut.mutate(row.key)}
                                disabled={disableMut.isPending}
                              >
                                禁用
                              </Button>
                            ) : (
                              <Button
                                size="sm"
                                onClick={() => enableMut.mutate(row.key)}
                                disabled={enableMut.isPending}
                              >
                                启用
                              </Button>
                            )}
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                if (!confirm(`确认卸载插件「${row.key}」？`)) return;
                                uninstallMut.mutate(row.key);
                              }}
                              disabled={uninstallMut.isPending}
                            >
                              卸载
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Tab 3：开发指南 ────────────────────────────────────────────────
// docs/PLUGIN-DEV-GUIDE.md 在 build 时通过 vite raw import 进 bundle，
// 用 react-markdown + remark-gfm（GFM 表格 / 删除线）+ rehype-highlight（代码高亮）渲染。
function DevGuideTab() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">插件开发指南</CardTitle>
        <CardDescription>
          源文件：<code>docs/PLUGIN-DEV-GUIDE.md</code>，构建时打包进前端
        </CardDescription>
      </CardHeader>
      <CardContent>
        <article className="prose prose-sm max-w-none dark:prose-invert">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
          >
            {devGuideMd}
          </ReactMarkdown>
        </article>
      </CardContent>
    </Card>
  );
}
