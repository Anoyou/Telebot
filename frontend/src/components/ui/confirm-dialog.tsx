// 统一确认对话框（第 20 轮 Web 信息架构第二批）：替换浏览器原生 confirm()。
//
// 用法：
//   if (!(await confirmDialog({ title: "删除规则？", description: "删除后不可恢复。", confirmLabel: "删除", tone: "danger" }))) return;
//
// <ConfirmDialogHost /> 挂在应用根部（main.tsx）。未挂载时（如单元测试）回退到 window.confirm，保证行为不丢。
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, HelpCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export interface ConfirmDialogOptions {
  /** 标题：一句话说清要做什么，例如「删除规则「早安」？」 */
  title: string;
  /** 后果说明：会发生什么、能否恢复。 */
  description?: ReactNode;
  /** 确认按钮文字，默认「确认」。 */
  confirmLabel?: string;
  /** 取消按钮文字，默认「取消」。 */
  cancelLabel?: string;
  /** danger：红色确认按钮 + 警示图标（删除、清空、停止等不可逆操作）。 */
  tone?: "danger" | "default";
}

interface PendingConfirm extends ConfirmDialogOptions {
  id: number;
  resolve: (ok: boolean) => void;
}

type Listener = (request: PendingConfirm) => void;

let listener: Listener | null = null;
let nextId = 1;

function plainText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(plainText).join("");
  return "";
}

/** 打开统一确认框，用户点确认返回 true，取消 / 关闭返回 false。 */
export function confirmDialog(options: ConfirmDialogOptions): Promise<boolean> {
  if (!listener) {
    const text = [options.title, plainText(options.description)].filter(Boolean).join("\n");
    return Promise.resolve(typeof window !== "undefined" ? window.confirm(text) : false);
  }
  const current = listener;
  return new Promise<boolean>((resolve) => {
    current({ ...options, id: nextId++, resolve });
  });
}

export function ConfirmDialogHost() {
  const [queue, setQueue] = useState<PendingConfirm[]>([]);
  const current = queue[0] ?? null;
  const settledRef = useRef<number | null>(null);

  useEffect(() => {
    const handler: Listener = (request) => setQueue((items) => [...items, request]);
    listener = handler;
    return () => {
      if (listener === handler) listener = null;
    };
  }, []);

  const settle = (ok: boolean) => {
    if (!current || settledRef.current === current.id) return;
    settledRef.current = current.id;
    current.resolve(ok);
    setQueue((items) => items.slice(1));
  };

  const danger = current?.tone === "danger";
  const Icon = danger ? AlertTriangle : HelpCircle;

  return (
    <Dialog
      open={Boolean(current)}
      onOpenChange={(open) => {
        if (!open) settle(false);
      }}
    >
      {current ? (
        <DialogContent
          key={current.id}
          data-confirm-dialog
          data-confirm-tone={danger ? "danger" : "default"}
          className="max-w-md"
          onOpenAutoFocus={(event) => {
            // 焦点默认放在「取消」上，避免回车误触危险操作
            event.preventDefault();
            const root = event.currentTarget as HTMLElement | null;
            root?.querySelector<HTMLButtonElement>("[data-confirm-cancel]")?.focus();
          }}
        >
          <DialogHeader>
            <div className="flex items-start gap-3 pr-6">
              <span
                className={cn(
                  "mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-full",
                  danger ? "bg-destructive/10 text-destructive" : "bg-primary/10 text-primary",
                )}
              >
                <Icon className="h-4 w-4" />
              </span>
              <div className="min-w-0 space-y-1.5">
                <DialogTitle className="text-base leading-6">{current.title}</DialogTitle>
                {current.description ? (
                  <DialogDescription className="whitespace-pre-line leading-6">{current.description}</DialogDescription>
                ) : (
                  <DialogDescription className="sr-only">请确认是否继续</DialogDescription>
                )}
              </div>
            </div>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" data-confirm-cancel onClick={() => settle(false)}>
              {current.cancelLabel ?? "取消"}
            </Button>
            <Button
              type="button"
              variant={danger ? "destructive" : "default"}
              data-confirm-ok
              onClick={() => settle(true)}
            >
              {current.confirmLabel ?? "确认"}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
