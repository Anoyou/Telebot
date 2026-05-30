import type { ComponentType, ReactNode } from "react";

import {
  Card,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

export type VisualTone = "primary" | "success" | "warn" | "danger" | "neutral";

type ToneClasses = {
  rail: string;
  iconWrap: string;
  icon: string;
  pill: string;
  dot: string;
  bar: string;
};

export function toneClasses(tone: VisualTone): ToneClasses {
  const map: Record<VisualTone, ToneClasses> = {
    primary: {
      rail: "bg-primary",
      iconWrap: "bg-primary/10",
      icon: "text-primary",
      pill: "border-primary/20 bg-primary/10",
      dot: "bg-primary",
      bar: "bg-primary",
    },
    success: {
      rail: "bg-emerald-500",
      iconWrap: "bg-emerald-500/10",
      icon: "text-emerald-600 dark:text-emerald-300",
      pill: "border-emerald-500/20 bg-emerald-500/10",
      dot: "bg-emerald-500",
      bar: "bg-emerald-500",
    },
    warn: {
      rail: "bg-amber-500",
      iconWrap: "bg-amber-500/10",
      icon: "text-amber-600 dark:text-amber-300",
      pill: "border-amber-500/25 bg-amber-500/10",
      dot: "bg-amber-500",
      bar: "bg-amber-500",
    },
    danger: {
      rail: "bg-rose-500",
      iconWrap: "bg-rose-500/10",
      icon: "text-rose-600 dark:text-rose-300",
      pill: "border-rose-500/25 bg-rose-500/10",
      dot: "bg-rose-500",
      bar: "bg-rose-500",
    },
    neutral: {
      rail: "bg-border",
      iconWrap: "bg-muted",
      icon: "text-muted-foreground",
      pill: "border-border/70 bg-background/80",
      dot: "bg-muted-foreground",
      bar: "bg-muted-foreground",
    },
  };
  return map[tone];
}

export function SignalPill({
  tone,
  label,
  value,
  className,
}: {
  tone: VisualTone;
  label: string;
  value: ReactNode;
  className?: string;
}) {
  const toneClass = toneClasses(tone);
  return (
    <div
      className={cn(
        "inline-flex min-h-9 max-w-full items-center gap-2 whitespace-nowrap rounded-full border px-3 text-xs shadow-sm",
        toneClass.pill,
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", toneClass.dot)} />
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 truncate font-semibold text-foreground">{value}</span>
    </div>
  );
}

export function MeterBar({
  value,
  tone = "neutral",
  className,
}: {
  value?: number | null;
  tone?: VisualTone;
  className?: string;
}) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return null;
  }
  const toneClass = toneClasses(tone);
  return (
    <div className={cn("h-1.5 overflow-hidden rounded-full bg-background", className)}>
      <div
        className={cn("h-full rounded-full transition-[width] duration-300", toneClass.bar)}
        style={{ width: `${clamp(value, 2, 100)}%` }}
      />
    </div>
  );
}

export function ToneRailCard({
  icon: Icon,
  title,
  value,
  description,
  tone = "neutral",
  className,
  valueClassName,
}: {
  icon: ComponentType<{ className?: string }>;
  title: ReactNode;
  value: ReactNode;
  description?: ReactNode;
  tone?: VisualTone;
  className?: string;
  valueClassName?: string;
}) {
  const toneClass = toneClasses(tone);
  return (
    <Card
      className={cn(
        "group relative h-full overflow-hidden transition duration-200 hover:-translate-y-0.5 hover:shadow-[0_1px_2px_hsl(220_20%_20%/0.04),0_22px_54px_hsl(220_20%_20%/0.09)]",
        className,
      )}
    >
      <div className={cn("absolute inset-x-0 top-0 h-1", toneClass.rail)} />
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="min-w-0">
          <CardTitle className="inline-flex max-w-full items-center gap-2 truncate">
            <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-lg", toneClass.iconWrap)}>
              <Icon className={cn("h-4 w-4", toneClass.icon)} />
            </span>
            <span className="truncate">{title}</span>
          </CardTitle>
          {description ? (
            <CardDescription className="mt-3 text-sm leading-5">
              {description}
            </CardDescription>
          ) : null}
        </div>
      </CardHeader>
      <CardFooter className="pt-0">
        <div
          className={cn(
            "min-w-0",
            valueClassName ?? "truncate text-2xl font-bold tracking-tight",
          )}
        >
          {value}
        </div>
      </CardFooter>
    </Card>
  );
}

export function StatusSummaryPanel({
  icon: Icon,
  title,
  description,
  signals,
  aside,
  actions,
  className,
  titleLevel = "h1",
}: {
  icon: ComponentType<{ className?: string }>;
  title: ReactNode;
  description: ReactNode;
  signals?: ReactNode;
  aside?: ReactNode;
  actions?: ReactNode;
  className?: string;
  titleLevel?: "h1" | "h2";
}) {
  const Heading = titleLevel;
  return (
    <section
      className={cn(
        "relative overflow-hidden rounded-lg border border-border/80 bg-card shadow-[0_1px_2px_hsl(220_20%_20%/0.04),0_20px_56px_hsl(220_20%_20%/0.07)]",
        className,
      )}
    >
      <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(circle_at_8%_0%,hsl(var(--primary)/0.10),transparent_28rem),linear-gradient(115deg,hsl(var(--card)),hsl(var(--muted)/0.45))]" />
      <div className="relative grid gap-6 p-5 lg:grid-cols-[minmax(0,1fr)_auto] md:p-6 lg:p-7">
        <div className="min-w-0">
          <div className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-border/70 bg-background/80 text-primary shadow-sm">
            <Icon className="h-5 w-5" />
          </div>
          <Heading className="mt-4 text-3xl font-bold tracking-tight text-foreground">
            {title}
          </Heading>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground md:text-base">
            {description}
          </p>
          {signals ? <div className="mt-5 flex flex-wrap gap-2">{signals}</div> : null}
        </div>
        {(aside || actions) ? (
          <div className="flex flex-col justify-between gap-4 lg:min-w-64 lg:items-end">
            {aside}
            {actions ? <div className="flex flex-wrap gap-2 lg:justify-end">{actions}</div> : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}

export function SectionHeader({
  icon: Icon,
  title,
  description,
  actions,
  meta,
  className,
}: {
  icon?: ComponentType<{ className?: string }>;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-3 md:flex-row md:items-start md:justify-between", className)}>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          {Icon ? <Icon className="h-4 w-4 shrink-0 text-primary" /> : null}
          <div className="min-w-0 truncate text-base font-semibold tracking-tight">
            {title}
          </div>
        </div>
        {description ? (
          <div className="mt-1 text-sm leading-5 text-muted-foreground">
            {description}
          </div>
        ) : null}
      </div>
      {(meta || actions) ? (
        <div className="flex w-full min-w-0 flex-wrap items-center gap-2 md:w-auto md:shrink-0 md:justify-end">
          {meta}
          {actions}
        </div>
      ) : null}
    </div>
  );
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}
