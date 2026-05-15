import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { goBackOr } from "@/lib/navigation";
import { LLMProviders } from "@/pages/AI/LLMProviders";

export function AIProviders() {
  const nav = useNavigate();

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={() => goBackOr(nav, "/ai")}>
        <ArrowLeft className="mr-1 h-4 w-4" /> 返回上一页
      </Button>
      <LLMProviders />
    </div>
  );
}
