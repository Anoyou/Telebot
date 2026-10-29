import { useMutation, useQuery } from "@tanstack/react-query";
import { AppWindow, Play, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  callPluginUIBridge,
  listPluginUIEntries,
  openPluginUIPage,
  runPluginUIMenuAction,
  type PluginUIMenu,
  type PluginUIMenuAction,
  type PluginUIPageEntry,
} from "@/api/plugins";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/misc";
import { API_BASE, getErrMsg } from "@/lib/api";
import {
  bridgeError,
  bridgeOk,
  localBridgeResult,
  PAGE_SANDBOX,
  parseBridgeRequest,
  themeEvent,
  type BridgeReply,
} from "@/lib/pluginPageBridge";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

// 插件自带页面与菜单项（ui.pages / ui.actions，Plugin API 1.4）。
// 插件没有声明时整块不渲染；页面在 iframe sandbox="allow-scripts"（无同源）里运行，只能经 postMessage Bridge 访问宿主。

type OpenPage = { pluginKey: string; pageId: string; title: string; subtitle?: string | null };

export function PluginPageEntries({
  menu,
  pluginKey,
  accountId,
  className,
}: {
  menu: PluginUIMenu;
  pluginKey?: string;
  accountId?: number | null;
  className?: string;
}) {
  const entriesQ = useQuery({
    queryKey: ["plugin-ui-entries", menu, pluginKey ?? null, accountId ?? null],
    queryFn: () => listPluginUIEntries({ menu, pluginKey, accountId }),
    retry: false,
    staleTime: 60_000,
  });
  const [openPage, setOpenPage] = useState<OpenPage | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<PluginUIMenuAction | null>(null);
  const actionMut = useMutation({
    mutationFn: (action: PluginUIMenuAction) => runPluginUIMenuAction(action, accountId),
    onSuccess: (result) => {
      if (result.ok === false) toast.error(result.message || "插件没有完成这个操作");
      else toast.success(result.message || "已完成");
    },
    onError: (error) => toast.error(`操作失败：${getErrMsg(error)}`),
  });
  // 旧服务器没有这个接口（404）或读取失败：不显示入口，不打扰旧插件。
  const pages = entriesQ.data?.pages ?? [];
  const actions = entriesQ.data?.actions ?? [];
  if (entriesQ.isLoading || entriesQ.isError || (pages.length === 0 && actions.length === 0)) return null;

  const showPluginName = !pluginKey;
  const openEntry = (entry: PluginUIPageEntry) =>
    setOpenPage({ pluginKey: entry.plugin_key, pageId: entry.page_id, title: entry.title, subtitle: entry.subtitle });
  const runAction = (action: PluginUIMenuAction) => {
    if (action.page_id) {
      const page = pages.find((item) => item.plugin_key === action.plugin_key && item.page_id === action.page_id);
      setOpenPage({ pluginKey: action.plugin_key, pageId: action.page_id, title: page?.title ?? action.title, subtitle: page?.subtitle });
      return;
    }
    if (action.confirm || action.destructive) {
      setPendingConfirm(action);
      return;
    }
    actionMut.mutate(action);
  };

  return (
    <section className={cn("rounded-md border border-border/70 bg-background/80 p-4", className)} data-plugin-ui-entries={menu}>
      <div className="mb-2 text-sm font-medium">插件页面与操作</div>
      <div className="flex flex-col gap-1">
        {pages.map((entry) => (
          <button
            key={`page:${entry.plugin_key}:${entry.page_id}`}
            type="button"
            className="flex min-h-11 items-center gap-3 rounded-md px-2 text-left text-sm hover:bg-muted/60"
            onClick={() => openEntry(entry)}
            data-plugin-page={entry.page_id}
          >
            <AppWindow className="h-4 w-4 shrink-0 text-primary" aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block truncate">{entry.title}</span>
              {entry.subtitle || showPluginName ? (
                <span className="block truncate text-xs text-muted-foreground">
                  {[showPluginName ? entry.plugin_name : null, entry.subtitle].filter(Boolean).join(" · ")}
                </span>
              ) : null}
            </span>
          </button>
        ))}
        {actions.map((action) => (
          <button
            key={`action:${action.plugin_key}:${action.action_id}`}
            type="button"
            className={cn(
              "flex min-h-11 items-center gap-3 rounded-md px-2 text-left text-sm hover:bg-muted/60 disabled:opacity-60",
              action.destructive && "text-destructive",
            )}
            disabled={actionMut.isPending}
            onClick={() => runAction(action)}
            data-plugin-menu-action={action.action_id}
          >
            {action.page_id ? (
              <AppWindow className="h-4 w-4 shrink-0 text-primary" aria-hidden />
            ) : action.destructive ? (
              <TriangleAlert className="h-4 w-4 shrink-0" aria-hidden />
            ) : (
              <Play className="h-4 w-4 shrink-0 text-primary" aria-hidden />
            )}
            <span className="min-w-0 flex-1">
              <span className="block truncate">{action.title}</span>
              {showPluginName ? <span className="block truncate text-xs text-muted-foreground">{action.plugin_name}</span> : null}
            </span>
            {actionMut.isPending && actionMut.variables?.action_id === action.action_id ? <Spinner className="h-4 w-4" /> : null}
          </button>
        ))}
      </div>

      <Dialog open={pendingConfirm !== null} onOpenChange={(open) => !open && setPendingConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{pendingConfirm?.title}</DialogTitle>
            <DialogDescription>
              {typeof pendingConfirm?.confirm === "string" && pendingConfirm.confirm ? pendingConfirm.confirm : "确定执行这个插件操作吗？不会自动重试。"}
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setPendingConfirm(null)}>取消</Button>
            <Button
              variant={pendingConfirm?.destructive ? "destructive" : "default"}
              onClick={() => {
                if (pendingConfirm) actionMut.mutate(pendingConfirm);
                setPendingConfirm(null);
              }}
            >
              确定
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={openPage !== null} onOpenChange={(open) => !open && setOpenPage(null)}>
        <DialogContent className="flex h-[calc(100dvh-2rem)] max-w-3xl flex-col gap-3 p-3 sm:h-[80vh] sm:p-4">
          <DialogHeader className="pr-8">
            <DialogTitle>{openPage?.title}</DialogTitle>
            <DialogDescription>{openPage?.subtitle || "插件自带页面 · 在沙箱中运行，只能读取页面声明的数据"}</DialogDescription>
          </DialogHeader>
          {openPage ? <PluginPageFrame page={openPage} accountId={accountId} /> : null}
        </DialogContent>
      </Dialog>
    </section>
  );
}

function pageUrl(url: string): string {
  if (/^https?:\/\//.test(url)) return url;
  const base = (API_BASE || "").replace(/\/$/, "");
  return `${base}${url}`;
}

export function PluginPageFrame({ page, accountId }: { page: OpenPage; accountId?: number | null }) {
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const { resolvedTheme } = useTheme();
  const scheme: "light" | "dark" = resolvedTheme === "dark" ? "dark" : "light";
  const schemeRef = useRef(scheme);
  schemeRef.current = scheme;
  const sessionQ = useQuery({
    queryKey: ["plugin-ui-page-session", page.pluginKey, page.pageId, accountId ?? null],
    queryFn: () => openPluginUIPage(page.pluginKey, page.pageId, accountId),
    retry: false,
    staleTime: 30 * 60_000,
    gcTime: 0,
  });

  const reply = useCallback((message: BridgeReply | ReturnType<typeof themeEvent>) => {
    // 沙箱页面是不透明来源（origin 为 "null"），只能用 "*"；只发给本页面的 window。
    frameRef.current?.contentWindow?.postMessage(message, "*");
  }, []);

  useEffect(() => {
    const onMessage = async (event: MessageEvent) => {
      const frameWindow = frameRef.current?.contentWindow;
      if (!frameWindow || event.source !== frameWindow) return;
      const request = parseBridgeRequest(event.data);
      if (!request) return;
      const local = localBridgeResult(request.method, schemeRef.current);
      if (local.handled) {
        reply(bridgeOk(request.id, local.result));
        return;
      }
      try {
        const response = await callPluginUIBridge(page.pluginKey, page.pageId, request.method, request.params, accountId);
        reply(
          response.ok
            ? bridgeOk(request.id, response.result)
            : bridgeError(request.id, response.error?.code ?? "BRIDGE_FAILED", response.error?.message ?? "请求失败"),
        );
      } catch (error) {
        reply(bridgeError(request.id, "BRIDGE_HOST_FAILED", getErrMsg(error)));
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [accountId, page.pageId, page.pluginKey, reply]);

  useEffect(() => {
    reply(themeEvent(scheme));
  }, [reply, scheme]);

  if (sessionQ.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }
  if (sessionQ.isError || !sessionQ.data) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-destructive" role="alert">
        <span>页面打开失败：{getErrMsg(sessionQ.error)}</span>
        <Button variant="outline" size="sm" onClick={() => sessionQ.refetch()}>重试</Button>
      </div>
    );
  }
  return (
    <iframe
      ref={frameRef}
      title={page.title}
      src={pageUrl(sessionQ.data.url)}
      sandbox={PAGE_SANDBOX}
      referrerPolicy="no-referrer"
      allow=""
      className="min-h-0 w-full flex-1 rounded-md border border-border/70 bg-background"
      data-plugin-page-frame={page.pageId}
      onLoad={() => reply(themeEvent(schemeRef.current))}
    />
  );
}
