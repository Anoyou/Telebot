// 按钮组件：variant + size，参考 shadcn/ui
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { Spinner } from "@/components/ui/misc";

const buttonVariants = cva(
  // 触控命中区（移动端）：`relative` + `after` 伪元素在按钮外扩一层透明命中盒。
  // 只在 `max-sm` 给高度 40px（配合 -mt-5 垂直居中），因此：
  //   - 视觉尺寸、布局、间距完全不变（伪元素无背景、无边框、不占布局）；
  //   - `>=640px` 的桌面布局下伪元素高度为 0（空 content、未设高度），不改变命中区；
  //   - 已经 >=40px 高的尺寸（lg / touch）伪元素整体落在按钮内部，等于无操作。
  // 宽度方向刻意不扩：横向外扩会抢占相邻按钮的命中区。
  "relative inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-[13px] font-semibold transition-all focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/45 disabled:pointer-events-none disabled:opacity-45 active:scale-[0.975] after:absolute after:inset-x-0 after:top-1/2 after:content-[''] max-sm:after:-mt-5 max-sm:after:h-10",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-sm hover:bg-primary-hover",
        destructive:
          "bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive-hover",
        softDestructive: "bg-destructive/15 text-destructive hover:bg-destructive/25",
        outline:
          "border border-border bg-card hover:bg-accent hover:text-accent-foreground",
        secondary: "bg-secondary text-secondary-foreground hover:bg-secondary-hover",
        tinted: "bg-primary/10 text-primary hover:bg-primary/[0.18]",
        quiet: "bg-muted text-foreground hover:bg-accent",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "bg-primary/10 text-primary hover:bg-primary/[0.18]",
      },
      size: {
        default: "h-[34px] px-4",
        sm: "h-8 rounded-[7px] px-3 text-xs",
        lg: "h-10 rounded-[9px] px-5 text-sm",
        touch: "h-11 rounded-[9px] px-5 text-sm",
        icon: "h-[34px] w-[34px]",
      },
    },
    compoundVariants: [
      { variant: "link", class: "h-auto rounded-sm px-2.5 py-1" },
    ],
    defaultVariants: { variant: "default", size: "default" },
  },
);

type ButtonBaseProps = React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>;

export type ButtonProps =
  | (ButtonBaseProps & {
      asChild?: false;
      loading?: boolean;
      loadingText?: React.ReactNode;
    })
  | (Omit<ButtonBaseProps, "disabled"> & {
      asChild: true;
      disabled?: never;
      loading?: never;
      loadingText?: never;
    });

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, loading = false, loadingText, disabled, children, ...props }, ref) => {
    if (asChild) {
      return (
        <Slot
          ref={ref}
          className={cn(buttonVariants({ variant, size, className }))}
          {...props}
        >
          {children}
        </Slot>
      );
    }
    return (
      <button
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        aria-busy={loading || undefined}
        disabled={disabled || loading}
        {...props}
      >
        {loading ? <Spinner size="sm" /> : null}
        {loading && loadingText ? loadingText : children}
      </button>
    );
  },
);
Button.displayName = "Button";
export { buttonVariants };
