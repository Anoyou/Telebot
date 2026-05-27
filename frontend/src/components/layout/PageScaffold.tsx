import type { ComponentType, ReactNode } from "react";

import { cn } from "@/lib/utils";

type PageShellProps = {
  children: ReactNode;
  className?: string;
};

export function PageShell({ children, className }: PageShellProps) {
  return (
    <div className={cn("w-full min-w-0 space-y-6", className)}>
      {children}
    </div>
  );
}

type PageHeaderProps = {
  title: string;
  description: ReactNode;
  icon: ComponentType<{ className?: string }>;
  actions?: ReactNode;
  size?: "default" | "hero";
};

export function PageHeader({
  title,
  description,
  icon: Icon,
  actions,
  size = "default",
}: PageHeaderProps) {
  const hero = size === "hero";
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1
          className={cn(
            "inline-flex items-center gap-2 tracking-tight",
            hero ? "text-3xl font-bold" : "text-2xl font-semibold",
          )}
        >
          <Icon className={cn("text-primary", hero ? "h-6 w-6" : "h-5 w-5")} />
          {title}
        </h1>
        <p className={cn("text-muted-foreground", hero ? "mt-1 text-base" : "text-sm")}>
          {description}
        </p>
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}
