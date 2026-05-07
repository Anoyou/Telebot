// 左侧导航：
//  - <Sidebar> 桌面端（≥md）常驻显示
//  - <MobileSidebar> 移动端通过抽屉模式呈现（Radix Dialog 实现，左侧滑入）
// 两者共享 NavList，移动端点击导航后自动关闭抽屉。
import { NavLink } from "react-router-dom";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  Boxes,
  Cog,
  LayoutDashboard,
  LayoutTemplate,
  ScrollText,
  Sparkles,
  Users,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { APP_VERSION_LABEL } from "@/lib/version";

interface NavItem {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  end?: boolean;
}

// 顶层导航条目；
// 0.4.1: /matrix + /plugins 合并到 /plugins（功能矩阵 / 插件 / 开发指南 三 tab）
const NAV: NavItem[] = [
  { to: "/", label: "概览", icon: LayoutDashboard, end: true },
  { to: "/accounts", label: "账号", icon: Users },
  { to: "/plugins", label: "插件", icon: Boxes },
  { to: "/templates", label: "模板", icon: LayoutTemplate },
  { to: "/ai", label: "AI", icon: Sparkles },
  { to: "/logs", label: "日志", icon: ScrollText },
  { to: "/settings", label: "系统", icon: Cog },
];

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex-1 space-y-1 overflow-y-auto p-3 text-sm">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2 rounded-md px-3 py-2 text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              isActive && "bg-accent text-accent-foreground",
            )
          }
        >
          <item.icon className="h-4 w-4" />
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      <div className="flex h-14 shrink-0 items-center border-b px-4 text-base font-semibold">
        Telegram Userbot
      </div>
      <NavList onNavigate={onNavigate} />
      <div className="shrink-0 border-t p-3 text-xs text-muted-foreground">
        {APP_VERSION_LABEL}
      </div>
    </>
  );
}

// 桌面常驻侧栏：< md 隐藏，由 MobileSidebar 接管
export function Sidebar() {
  return (
    <aside className="hidden w-56 shrink-0 flex-col border-r bg-card md:flex">
      <SidebarBody />
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
            "data-[state=closed]:opacity-0 data-[state=open]:opacity-100",
          )}
        />
        <DialogPrimitive.Content
          className={cn(
            "fixed inset-y-0 left-0 z-50 flex w-64 max-w-[80vw] flex-col border-r bg-card shadow-lg md:hidden",
            // 安全区适配：iPhone 横屏时左侧刘海，全屏 PWA 顶/底状态栏区
            "pl-[env(safe-area-inset-left)] pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]",
            "transition-transform duration-200 ease-out",
            "data-[state=closed]:-translate-x-full data-[state=open]:translate-x-0",
          )}
          // 屏幕阅读器需要 Title；视觉上隐藏
          aria-describedby={undefined}
        >
          <DialogPrimitive.Title className="sr-only">导航菜单</DialogPrimitive.Title>
          <DialogPrimitive.Close
            className="absolute right-2 top-[calc(env(safe-area-inset-top)+0.5rem)] rounded-sm p-1 text-muted-foreground hover:text-foreground"
            aria-label="关闭菜单"
          >
            <X className="h-4 w-4" />
          </DialogPrimitive.Close>
          <SidebarBody onNavigate={() => onOpenChange(false)} />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
