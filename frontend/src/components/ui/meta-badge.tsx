import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type MetaBadgeTone = "neutral" | "success" | "warn" | "danger" | "outline";

const toneClass: Record<MetaBadgeTone, string> = {
  neutral: "border-transparent bg-muted text-foreground",
  success: "border-transparent bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  warn: "border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-300",
  danger: "border-transparent bg-destructive/15 text-destructive",
  outline: "border-border/80 bg-background text-foreground",
};

export function MetaBadge({
  tone = "neutral",
  mono = false,
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  tone?: MetaBadgeTone;
  mono?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold leading-5",
        mono && "font-mono",
        toneClass[tone],
        className,
      )}
      {...props}
    />
  );
}
