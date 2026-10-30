// 服务器设置 › 界面偏好（第 20 轮 Web 信息架构第二批）
//  - 常用入口：选 1–4 个一级/二级入口并排序，桌面侧栏「常用」组与手机底栏共用（ui_preferences.primary_nav）。
//  - 组内顺序：「管理」「工具与设置」两组内部的先后（ui_preferences.sidebar_order）。
// 旧服务器没有 primary_nav 字段时，保存常用入口会被拒，页面会提示更新服务器。
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, GripVertical, Minus, Plus, RotateCcw, Save } from "lucide-react";
import { toast } from "sonner";

import { patchSystemSettings } from "@/api/system";
import type { SystemSettings } from "@/api/types";
import { NAV_BY_PATH } from "@/components/layout/Sidebar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getErrMsg } from "@/lib/api";
import {
  DEFAULT_PRIMARY_NAV,
  MANAGE_NAV_DEFAULT_ORDER,
  NAV_GROUP_LABELS,
  PRIMARY_NAV_ELIGIBLE,
  PRIMARY_NAV_MAX,
  TOOLS_NAV_DEFAULT_ORDER,
  normalizePrimaryNav,
  sortByPreference,
} from "@/lib/navGroups";
import { cn } from "@/lib/utils";

const GROUP_ORDER_DEFAULT = [...MANAGE_NAV_DEFAULT_ORDER, ...TOOLS_NAV_DEFAULT_ORDER];

export function NavigationPreferences({ settings }: { settings?: SystemSettings }) {
  const queryClient = useQueryClient();
  const savedPrimary = settings?.ui_preferences?.primary_nav;
  const savedOrder = settings?.ui_preferences?.sidebar_order;
  const [primary, setPrimary] = useState<string[]>(() => normalizePrimaryNav(savedPrimary));
  const [groupOrder, setGroupOrder] = useState<string[]>(() => sortByPreference(GROUP_ORDER_DEFAULT, savedOrder));

  useEffect(() => {
    setPrimary(normalizePrimaryNav(savedPrimary));
    setGroupOrder(sortByPreference(GROUP_ORDER_DEFAULT, savedOrder));
  }, [savedPrimary?.join("|"), savedOrder?.join("|")]);

  const primarySet = new Set(primary);
  const manageOrder = useMemo(
    () => groupOrder.filter((path) => (MANAGE_NAV_DEFAULT_ORDER as readonly string[]).includes(path) && !primarySet.has(path)),
    [groupOrder, primary],
  );
  const toolsOrder = useMemo(
    () => groupOrder.filter((path) => (TOOLS_NAV_DEFAULT_ORDER as readonly string[]).includes(path)),
    [groupOrder],
  );

  const saveMutation = useMutation({
    mutationFn: () => patchSystemSettings({
      ui_preferences: {
        primary_nav: primary,
        sidebar_order: groupOrder,
      },
    }),
    onSuccess: () => {
      toast.success("界面偏好已保存，侧栏和手机底栏已同步更新");
      void queryClient.invalidateQueries({ queryKey: ["system", "settings"] });
    },
    onError: (error) => toast.error(getErrMsg(error)),
  });

  const togglePrimary = (path: string) => {
    if (primarySet.has(path)) {
      if (primary.length <= 1) {
        toast.info("常用入口至少保留 1 个");
        return;
      }
      setPrimary(primary.filter((item) => item !== path));
      return;
    }
    if (primary.length >= PRIMARY_NAV_MAX) {
      toast.info(`常用入口最多 ${PRIMARY_NAV_MAX} 个，先移出一个再添加`);
      return;
    }
    setPrimary([...primary, path]);
  };

  const replaceGroup = (current: string[], next: string[]) => {
    const set = new Set(current);
    const queue = [...next];
    setGroupOrder(groupOrder.map((path) => (set.has(path) ? queue.shift() ?? path : path)));
  };

  return (
    <Card data-settings-interface>
      <CardHeader>
        <CardTitle className="text-base">常用入口与导航顺序</CardTitle>
        <CardDescription>
          「常用入口」同时决定桌面侧栏最上面一组和手机底栏，最多 {PRIMARY_NAV_MAX} 个；没选进常用的入口会回到「管理」组，任何功能都找得到。
          Agent 固定在侧栏顶部和手机底栏右侧，不参与排序。保存后跟随账号跨设备使用。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-4">
            <OrderEditor
              title={`${NAV_GROUP_LABELS.primary}（桌面侧栏第一组 + 手机底栏）`}
              testId="primary"
              order={primary}
              onChange={setPrimary}
              renderAction={(path) => (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-9 w-9"
                  onClick={() => togglePrimary(path)}
                  aria-label={`从常用移出${NAV_BY_PATH.get(path)?.label ?? path}`}
                >
                  <Minus className="h-4 w-4" />
                </Button>
              )}
            />
            <div className="rounded-lg border border-dashed border-border/70 p-3">
              <div className="mb-2 text-sm font-medium">可以加入常用的入口</div>
              <div className="flex flex-wrap gap-2">
                {PRIMARY_NAV_ELIGIBLE.filter((path) => !primarySet.has(path)).map((path) => {
                  const item = NAV_BY_PATH.get(path);
                  if (!item) return null;
                  return (
                    <Button
                      key={path}
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => togglePrimary(path)}
                      aria-label={`加入常用：${item.label}`}
                      disabled={primary.length >= PRIMARY_NAV_MAX}
                    >
                      <Plus className="mr-1 h-3.5 w-3.5" />
                      {item.label}
                    </Button>
                  );
                })}
              </div>
              <p className="mt-2 text-xs text-muted-foreground">Webhook、命中调试、服务器设置属于低频工具，固定在「工具与设置」组。</p>
            </div>
          </div>
          <div className="space-y-4">
            <OrderEditor
              title={`${NAV_GROUP_LABELS.manage}（组内顺序）`}
              testId="manage"
              order={manageOrder}
              onChange={(next) => replaceGroup(manageOrder, next)}
            />
            <OrderEditor
              title={`${NAV_GROUP_LABELS.tools}（组内顺序）`}
              testId="tools"
              order={toolsOrder}
              onChange={(next) => replaceGroup(toolsOrder, next)}
            />
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setPrimary([...DEFAULT_PRIMARY_NAV]);
              setGroupOrder([...GROUP_ORDER_DEFAULT]);
            }}
          >
            <RotateCcw className="mr-1 h-4 w-4" />恢复默认
          </Button>
          <Button type="button" loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
            {!saveMutation.isPending ? <Save className="mr-1 h-4 w-4" /> : null}保存
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function OrderEditor({
  title,
  testId,
  order,
  onChange,
  renderAction,
}: {
  title: string;
  testId: string;
  order: string[];
  onChange: (next: string[]) => void;
  renderAction?: (path: string) => React.ReactNode;
}) {
  const [draggedPath, setDraggedPath] = useState<string | null>(null);
  const move = (path: string, offset: number) => {
    const index = order.indexOf(path);
    const target = index + offset;
    if (index < 0 || target < 0 || target >= order.length) return;
    const next = [...order];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };
  const placeBefore = (path: string, targetPath: string) => {
    if (path === targetPath || !order.includes(path)) return;
    const next = order.filter((item) => item !== path);
    next.splice(next.indexOf(targetPath), 0, path);
    onChange(next);
  };

  return (
    <div className="rounded-lg border border-border/70 bg-muted/15 p-3" data-nav-pref-group={testId}>
      <div className="mb-2 text-sm font-medium">{title}</div>
      {order.length === 0 ? (
        <p className="py-2 text-xs text-muted-foreground">这一组目前没有入口。</p>
      ) : null}
      <div className="space-y-1.5">
        {order.map((path, index) => {
          const item = NAV_BY_PATH.get(path);
          if (!item) return null;
          return (
            <div
              key={path}
              draggable
              onDragStart={() => setDraggedPath(path)}
              onDragEnd={() => setDraggedPath(null)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={() => {
                if (draggedPath) placeBefore(draggedPath, path);
                setDraggedPath(null);
              }}
              className={cn(
                "flex min-h-11 items-center gap-2 rounded-md border bg-background px-2",
                draggedPath === path && "opacity-50",
              )}
            >
              <GripVertical className="h-4 w-4 cursor-grab text-muted-foreground" />
              <item.icon className="h-4 w-4 text-primary" />
              <span className="min-w-0 flex-1 truncate text-sm">{item.label}</span>
              <Button type="button" variant="ghost" size="icon" className="h-9 w-9" disabled={index === 0} onClick={() => move(path, -1)} aria-label={`上移${item.label}`}>
                <ArrowUp className="h-4 w-4" />
              </Button>
              <Button type="button" variant="ghost" size="icon" className="h-9 w-9" disabled={index === order.length - 1} onClick={() => move(path, 1)} aria-label={`下移${item.label}`}>
                <ArrowDown className="h-4 w-4" />
              </Button>
              {renderAction?.(path)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
