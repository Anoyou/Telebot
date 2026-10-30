// 左侧导航（第 20 轮 Web 信息架构第二批）：
//  - <Sidebar> 桌面端（≥md）常驻显示
//  - <MobileSidebar> 平板浏览器（sm–md）通过抽屉呈现；手机（<sm）改由底栏 + 「更多」承担
// 结构：顶部固定「Agent」行（红点 = 待确认数）→ 常用 → 管理 → 工具与设置（可折叠）→ 使用指南 / 更新日志 / GitHub。
// 分组规则见 lib/navGroups.ts；导航名 = 页面标题，对照表见 ios/DESIGN.md。
import { lazy, Suspense, useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  BookOpen,
  Boxes,
  Bug,
  ChevronDown,
  Cog,
  GitFork,
  Github,
  GripVertical,
  Home,
  History,
  ListTodo,
  ScrollText,
  Sparkles,
  Users,
  WalletCards,
  Webhook,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { BrandLogo } from "@/components/BrandLogo";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
// DropdownMenuContent kept for Suspense fallback shell
import { cn } from "@/lib/utils";
import { formatRuntimeVersionLabel } from "@/lib/runtime-version";
import { getBackendVersion, getPlatformCapabilities, getSystemSettings, patchSystemSettings } from "@/api/system";
import { usePendingActionsCount } from "@/components/assistant/usePendingActionsCount";
import { getErrMsg } from "@/lib/api";
import {
  capabilityEnabledMap,
  filterNavByCapabilities,
  type CapabilityEnabledMap,
} from "@/lib/navigation";
import {
  NAV_GROUP_LABELS,
  groupNavPaths,
  mergeGroupOrder,
  normalizePrimaryNav,
  reorderWithinGroup,
  type NavGroupKey,
} from "@/lib/navGroups";
import { useAssistantDock } from "@/components/assistant/AssistantDock";
import { AgentMark } from "@/components/assistant/AgentMark";
const ChangelogMenu = lazy(() => import("./ChangelogMenu"));
const UsageGuideMenu = lazy(() => import("./UsageGuideMenu"));

export interface NavItem {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  end?: boolean;
}

// 全部可导航的一级页面（不含 Agent）。默认落地页是插件页。
// 导航名 = 页面标题（第 20 轮统一命名，见 ios/DESIGN.md「Web ↔ iOS 页面对照表」）。
// 「待确认」第 20 轮第二批起挂在 Agent 上，不再是独立侧栏项（/assistant/inbox 地址保留）。
export const NAV: NavItem[] = [
  { to: "/plugins", label: "插件", icon: Boxes },
  { to: "/interaction", label: "交互", icon: GitFork },
  { to: "/overview", label: "概览", icon: Home },
  { to: "/accounts", label: "账号", icon: Users },
  { to: "/ai", label: "AI", icon: Sparkles },
  { to: "/operations", label: "指令与任务", icon: ListTodo },
  { to: "/ledger", label: "资金台账", icon: WalletCards },
  { to: "/logs", label: "日志", icon: ScrollText },
  { to: "/webhooks", label: "Webhook", icon: Webhook },
  { to: "/dispatch-debug", label: "命中调试", icon: Bug },
  { to: "/settings", label: "服务器设置", icon: Cog },
];

export const NAV_BY_PATH = new Map(NAV.map((item) => [item.to, item]));

export function navForCapabilities(enabled: CapabilityEnabledMap): NavItem[] {
  return filterNavByCapabilities(NAV, enabled);
}

export type NavItemGroups = Record<NavGroupKey, NavItem[]>;

function toItems(paths: string[]): NavItem[] {
  return paths.map((path) => NAV_BY_PATH.get(path)).filter((item): item is NavItem => Boolean(item));
}

/** 按能力过滤 + 常用入口 + 组内顺序，算出三组导航。 */
export function navGroupsFor(
  enabled: CapabilityEnabledMap,
  prefs: { primaryNav?: string[] | null; sidebarOrder?: string[] | null } = {},
): NavItemGroups {
  const groups = groupNavPaths({
    visible: navForCapabilities(enabled).map((item) => item.to),
    primaryNav: prefs.primaryNav,
    sidebarOrder: prefs.sidebarOrder,
  });
  return {
    primary: toItems(groups.primary),
    manage: toItems(groups.manage),
    tools: toItems(groups.tools),
  };
}

/** 侧栏 / 手机底栏共用的导航数据：能力开关、常用入口、组内顺序、待确认数。 */
export function useNavModel() {
  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
    staleTime: 30_000,
  });
  const capsQ = useQuery({
    queryKey: ["system", "capabilities"],
    queryFn: getPlatformCapabilities,
    staleTime: 15_000,
  });
  const enabled = capabilityEnabledMap(capsQ.data, settingsQ.data?.ai_enabled ?? true);
  const agentEnabled = enabled.ai !== false;
  const pendingCount = usePendingActionsCount(agentEnabled);
  const prefs = settingsQ.data?.ui_preferences;
  const primaryNav = normalizePrimaryNav(prefs?.primary_nav);
  const sidebarOrder = prefs?.sidebar_order ?? [];
  return {
    enabled,
    agentEnabled,
    pendingCount,
    primaryNav,
    sidebarOrder,
    groups: navGroupsFor(enabled, { primaryNav, sidebarOrder }),
  };
}

export function pendingBadgeText(count: number): string {
  return count > 99 ? "99+" : String(count);
}

const TOOLS_COLLAPSED_KEY = "telepilot.sidebar.tools-collapsed";

function readToolsCollapsed(): boolean {
  try {
    return window.localStorage.getItem(TOOLS_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

/** 侧栏顶部固定的「Agent」行：点击打开/收起 Agent，红点 = 待确认数。AI 模块关闭时隐藏。 */
function AgentNavRow({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const { collapsed: dockCollapsed, setCollapsed } = useAssistantDock();
  const { agentEnabled, pendingCount } = useNavModel();
  const location = useLocation();
  if (!agentEnabled) return null;
  const active = !dockCollapsed || location.pathname === "/assistant" || location.pathname.startsWith("/assistant/");
  const label = pendingCount > 0 ? `Agent（${pendingCount} 条待确认）` : "Agent";
  return (
    <div className="px-4 pt-3">
      <button
        type="button"
        data-sidebar-agent
        aria-label={label}
        title={collapsed ? label : undefined}
        aria-pressed={!dockCollapsed}
        onClick={() => {
          setCollapsed(!dockCollapsed);
          onNavigate?.();
        }}
        className={cn(
          "liquid-sidebar-link relative flex h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-muted-foreground transition-all hover:text-accent-foreground",
          collapsed && "justify-center px-0",
          active && "liquid-sidebar-link-active text-accent-foreground",
        )}
      >
        <AgentMark className="h-5 w-5 shrink-0 text-primary" />
        <span className={cn("truncate font-medium", collapsed && "sr-only")}>Agent</span>
        {pendingCount > 0 ? (
          <span
            data-sidebar-agent-pending
            aria-hidden="true"
            className={cn(
              "inline-flex min-w-[1.15rem] items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium leading-4 text-destructive-foreground",
              collapsed ? "absolute right-1 top-1" : "ml-auto",
            )}
          >
            {pendingBadgeText(pendingCount)}
          </span>
        ) : null}
      </button>
    </div>
  );
}

function NavList({
  collapsed = false,
  onNavigate,
  reorderable = false,
}: {
  collapsed?: boolean;
  onNavigate?: () => void;
  reorderable?: boolean;
}) {
  const queryClient = useQueryClient();
  const location = useLocation();
  const [dragged, setDragged] = useState<{ path: string; group: NavGroupKey } | null>(null);
  const [draft, setDraft] = useState<{ primaryNav?: string[]; sidebarOrder?: string[] } | null>(null);
  const [toolsCollapsed, setToolsCollapsed] = useState(readToolsCollapsed);
  const model = useNavModel();
  const primaryNav = draft?.primaryNav ?? model.primaryNav;
  const sidebarOrder = draft?.sidebarOrder ?? model.sidebarOrder;
  const groups = navGroupsFor(model.enabled, { primaryNav, sidebarOrder });
  const toolsActive = groups.tools.some((item) => location.pathname === item.to || location.pathname.startsWith(`${item.to}/`));

  const saveOrder = useMutation({
    scope: { id: "sidebar-order" },
    mutationFn: (next: { primary_nav?: string[]; sidebar_order?: string[] }) => patchSystemSettings({ ui_preferences: next }),
    onSuccess: () => {
      setDraft(null);
      void queryClient.invalidateQueries({ queryKey: ["system", "settings"] });
    },
    onError: (error) => {
      setDraft(null);
      toast.error(`侧边栏顺序保存失败：${getErrMsg(error)}`);
    },
  });

  useEffect(() => {
    setDraft(null);
  }, [model.primaryNav.join("|"), model.sidebarOrder.join("|")]);

  const placeBefore = (group: NavGroupKey, path: string, target: string) => {
    const current = groups[group].map((item) => item.to);
    const next = reorderWithinGroup(current, path, target);
    if (!next) return;
    if (group === "primary") {
      // 常用组里可能有被模块开关隐藏的入口，保留它们的位置
      const merged = mergeGroupOrder(primaryNav, next);
      setDraft({ primaryNav: merged, sidebarOrder });
      saveOrder.mutate({ primary_nav: merged });
      return;
    }
    const full = [...new Set([...sidebarOrder, ...NAV.map((item) => item.to)])];
    const merged = mergeGroupOrder(full, next);
    setDraft({ primaryNav, sidebarOrder: merged });
    saveOrder.mutate({ sidebar_order: merged });
  };

  const setToolsCollapsedPersist = (value: boolean) => {
    setToolsCollapsed(value);
    try {
      window.localStorage.setItem(TOOLS_COLLAPSED_KEY, value ? "1" : "0");
    } catch {
      // 隐私模式下 localStorage 不可写，忽略即可
    }
  };

  const renderItem = (item: NavItem, group: NavGroupKey) => (
    <div
      key={item.to}
      className={cn("relative", dragged?.path === item.to && "opacity-45")}
      draggable={reorderable && !collapsed}
      data-sidebar-sort-path={item.to}
      data-sidebar-group={group}
      onDragStart={(event) => {
        setDragged({ path: item.to, group });
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", item.to);
      }}
      onDragEnd={() => setDragged(null)}
      onDragOver={(event) => {
        if (reorderable && dragged?.group === group) event.preventDefault();
      }}
      onDrop={(event) => {
        event.preventDefault();
        const source = dragged?.path || event.dataTransfer.getData("text/plain");
        if (source) placeBefore(group, source, item.to);
        setDragged(null);
      }}
    >
      {reorderable && !collapsed ? (
        <GripVertical className="pointer-events-none absolute left-1.5 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-muted-foreground/55" />
      ) : null}
      <NavLink
        to={item.to}
        end={item.end}
        onClick={onNavigate}
        aria-label={collapsed ? item.label : undefined}
        title={collapsed ? item.label : undefined}
        className={({ isActive }) =>
          cn(
            "liquid-sidebar-link relative flex h-11 items-center gap-3 rounded-lg px-3 text-muted-foreground transition-all hover:text-accent-foreground",
            reorderable && !collapsed && "pl-7",
            collapsed && "justify-center px-0",
            isActive && "liquid-sidebar-link-active text-accent-foreground",
          )
        }
      >
        <item.icon className="h-5 w-5 shrink-0" />
        <span className={cn("truncate", collapsed && "sr-only")}>{item.label}</span>
      </NavLink>
    </div>
  );

  const groupHeading = (group: NavGroupKey) =>
    collapsed ? (
      <div aria-hidden="true" className="mx-3 my-2 border-t border-border/60" />
    ) : (
      <div className="px-3 pb-1 pt-3 text-xs font-semibold tracking-wide text-muted-foreground">
        {NAV_GROUP_LABELS[group]}
      </div>
    );

  const toolsOpen = !toolsCollapsed || toolsActive || collapsed;

  return (
    <nav className="flex-1 overflow-y-auto px-4 pb-3 text-sm" aria-label="主导航">
      {groups.primary.length ? (
        <div role="group" aria-label={NAV_GROUP_LABELS.primary} data-sidebar-section="primary" className="space-y-1.5">
          {groupHeading("primary")}
          {groups.primary.map((item) => renderItem(item, "primary"))}
        </div>
      ) : null}
      {groups.manage.length ? (
        <div role="group" aria-label={NAV_GROUP_LABELS.manage} data-sidebar-section="manage" className="space-y-1.5">
          {groupHeading("manage")}
          {groups.manage.map((item) => renderItem(item, "manage"))}
        </div>
      ) : null}
      {groups.tools.length ? (
        <div role="group" aria-label={NAV_GROUP_LABELS.tools} data-sidebar-section="tools" className="space-y-1.5">
          {collapsed ? (
            groupHeading("tools")
          ) : (
            <button
              type="button"
              className="flex w-full items-center justify-between px-3 pb-1 pt-3 text-xs font-semibold tracking-wide text-muted-foreground hover:text-foreground"
              aria-expanded={toolsOpen}
              onClick={() => setToolsCollapsedPersist(!toolsCollapsed)}
            >
              <span>{NAV_GROUP_LABELS.tools}</span>
              <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", toolsOpen ? "rotate-0" : "-rotate-90")} />
            </button>
          )}
          {toolsOpen ? groups.tools.map((item) => renderItem(item, "tools")) : null}
        </div>
      ) : null}
    </nav>
  );
}

function SidebarBody({
  collapsed = false,
  mobile = false,
  onNavigate,
}: {
  collapsed?: boolean;
  mobile?: boolean;
  onNavigate?: () => void;
}) {
  const [changelogOpen, setChangelogOpen] = useState(false);
  const [usageGuideOpen, setUsageGuideOpen] = useState(false);
  const versionQ = useQuery({
    queryKey: ["system", "version"],
    queryFn: getBackendVersion,
    staleTime: 30_000,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
  const runtimeVersionLabel = formatRuntimeVersionLabel(
    versionQ.data,
    versionQ.isError ? "版本读取失败" : "正在读取…",
  );

  return (
    <>
      <div
        className={cn(
          "liquid-sidebar-header flex h-24 shrink-0 items-center px-5",
          collapsed && "justify-center px-3",
        )}
      >
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid h-10 w-10 shrink-0 place-items-center">
            <BrandLogo className="h-10 w-10 shadow-sm" />
          </div>
          <div className={cn("min-w-0", collapsed && "sr-only")}>
            <div className="truncate text-[1.55rem] font-bold leading-none tracking-tight">
              TelePilot
            </div>
            <div className="mt-1 text-xs font-medium text-muted-foreground">
              Telegram 控制台
            </div>
          </div>
        </div>
      </div>
      <AgentNavRow collapsed={collapsed} onNavigate={onNavigate} />
      <NavList collapsed={collapsed} onNavigate={onNavigate} reorderable={!mobile} />
      <div
        className={cn(
          "liquid-sidebar-footer shrink-0 space-y-2 px-4 py-5 text-sm text-muted-foreground",
          collapsed && "px-3",
        )}
      >
        <a
          href="https://github.com/Anoyou/Telebot"
          target="_blank"
          rel="noreferrer"
          className={cn(
            "liquid-sidebar-link flex h-11 items-center gap-3 rounded-lg px-3 transition-all hover:text-accent-foreground",
            collapsed && "justify-center px-0",
          )}
          aria-label="TelePilot GitHub"
          title="TelePilot GitHub"
        >
          <Github className="h-5 w-5 shrink-0" />
          <span className={cn("truncate", collapsed && "sr-only")}>TelePilot</span>
        </a>
        <DropdownMenu modal={false} open={usageGuideOpen} onOpenChange={setUsageGuideOpen}>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={cn(
                "liquid-sidebar-link flex h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-muted-foreground transition hover:text-accent-foreground",
                collapsed && "justify-center px-0",
              )}
              aria-label="使用指南"
              title="使用指南"
            >
              <BookOpen className="h-5 w-5 shrink-0" />
              <span className={cn("min-w-0 flex-1 truncate text-sm", collapsed && "sr-only")}>
                使用指南
              </span>
            </button>
          </DropdownMenuTrigger>
          {usageGuideOpen ? (
            <DropdownMenuContent
              side={mobile ? "top" : "right"}
              align={mobile ? "start" : "end"}
              sideOffset={10}
              collisionPadding={16}
              className="w-[min(28rem,calc(100vw-2rem))] p-0"
              style={{ maxHeight: "min(72dvh, 34rem)", overflowY: "auto" }}
            >
              <Suspense
                fallback={
                  <div className="p-4 text-sm text-muted-foreground">正在加载使用指南…</div>
                }
              >
                <UsageGuideMenu />
              </Suspense>
            </DropdownMenuContent>
          ) : null}
        </DropdownMenu>
        <DropdownMenu modal={false} open={changelogOpen} onOpenChange={setChangelogOpen}>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={cn(
                "liquid-sidebar-link flex h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-muted-foreground transition hover:text-accent-foreground",
                collapsed && "justify-center px-0",
              )}
              aria-label="更新日志"
              title="更新日志"
            >
              <History className="h-5 w-5 shrink-0" />
              <span className={cn("min-w-0 flex-1 truncate text-sm", collapsed && "sr-only")}>
                更新日志
              </span>
              <span
                className={cn("max-w-28 truncate text-xs font-medium", collapsed && "sr-only")}
                title={runtimeVersionLabel}
              >
                {runtimeVersionLabel}
              </span>
            </button>
          </DropdownMenuTrigger>
          {changelogOpen ? (
            <DropdownMenuContent
              side={mobile ? "top" : "right"}
              align={mobile ? "start" : "end"}
              sideOffset={10}
              collisionPadding={16}
              className="w-[min(28rem,calc(100vw-2rem))] p-0"
              style={{ maxHeight: "min(72dvh, 34rem)", overflowY: "auto" }}
            >
              <Suspense
                fallback={
                  <div className="p-4 text-sm text-muted-foreground">正在加载更新日志…</div>
                }
              >
                <ChangelogMenu />
              </Suspense>
            </DropdownMenuContent>
          ) : null}
        </DropdownMenu>
      </div>
    </>
  );
}

// 桌面常驻侧栏：< md 隐藏，由 MobileSidebar 接管
export function Sidebar({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <aside
      className={cn(
        "liquid-glass liquid-sidebar hidden shrink-0 flex-col md:flex",
        collapsed ? "w-[5.5rem]" : "w-[18rem]",
      )}
    >
      <SidebarBody collapsed={collapsed} />
    </aside>
  );
}

interface MobileSidebarProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// 移动端抽屉：从左滑入。点击导航链接自动关闭；点击遮罩 / Esc / 关闭按钮也会关闭。
// 动画用纯 CSS transition（不依赖 tailwindcss-animate 插件）。
export function MobileSidebar({ open, onOpenChange }: MobileSidebarProps) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            "fixed inset-0 z-50 bg-black/60 transition-opacity duration-200 md:hidden",
            "data-[state=closed]:pointer-events-none data-[state=closed]:opacity-0 data-[state=open]:opacity-100",
          )}
        />
        <DialogPrimitive.Content
          className={cn(
            "liquid-glass liquid-sidebar liquid-sidebar-drawer inset-y-0 left-0 z-[60] flex w-64 max-w-[80vw] flex-col md:hidden",
            // 安全区适配：iPhone 横屏时左侧刘海，全屏 PWA 顶/底状态栏区
            "pl-[env(safe-area-inset-left)] pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]",
            "data-[state=closed]:pointer-events-none",
          )}
          // 屏幕阅读器需要 Title；视觉上隐藏
          aria-describedby={undefined}
        >
          <DialogPrimitive.Title className="sr-only">导航菜单</DialogPrimitive.Title>
          <DialogPrimitive.Close
            className="absolute right-3 top-[calc(env(safe-area-inset-top)+0.75rem)] rounded-lg p-2 text-muted-foreground hover:bg-accent hover:text-foreground"
            aria-label="关闭菜单"
          >
            <X className="h-4 w-4" />
          </DialogPrimitive.Close>
          <SidebarBody mobile onNavigate={() => onOpenChange(false)} />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
