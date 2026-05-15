import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { goBackOr } from "@/lib/navigation";
import { AISettings } from "@/pages/AISettings";

// 先复用 AISettings 的帮助/说明内容，后续可将 guide/glossary/recommend 拆为独立组件。
export function AIHelp() {
  const nav = useNavigate();

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={() => goBackOr(nav, "/ai")}>
        <ArrowLeft className="mr-1 h-4 w-4" /> 返回上一页
      </Button>
      <AISettings />
    </div>
  );
}
