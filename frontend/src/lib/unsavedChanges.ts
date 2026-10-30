import { useEffect } from "react";

import { confirmDialog } from "@/components/ui/confirm-dialog";

/** 有未保存修改时弹统一确认框；没有修改直接返回 true。 */
export async function confirmDiscardChanges(dirty: boolean): Promise<boolean> {
  if (!dirty) return true;
  return confirmDialog({
    title: "放弃未保存的修改？",
    description: "当前页面有未保存的修改，离开后这些修改会丢失。",
    confirmLabel: "放弃修改",
    cancelLabel: "继续编辑",
    tone: "danger",
  });
}

// 用户已在统一确认框里同意离开后，重新触发同一个链接点击时放行一次。
let bypassNextLinkClick = false;

export function useUnsavedChanges(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return;

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const handleDocumentClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      const target = event.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest<HTMLAnchorElement>("a[href]");
      if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
      const destination = new URL(anchor.href, window.location.href);
      if (destination.origin !== window.location.origin || destination.href === window.location.href) return;
      if (bypassNextLinkClick) {
        bypassNextLinkClick = false;
        return;
      }
      // 先拦下导航，等用户在确认框里选择；同意后重新点击同一个链接。
      event.preventDefault();
      event.stopPropagation();
      void confirmDiscardChanges(true).then((ok) => {
        if (!ok) return;
        bypassNextLinkClick = true;
        anchor.click();
      });
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    document.addEventListener("click", handleDocumentClick, true);
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      document.removeEventListener("click", handleDocumentClick, true);
    };
  }, [dirty]);
}
