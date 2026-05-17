import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, Eye } from "lucide-react";

import { listLLMProviders } from "@/api/commands";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import { goBackOr } from "@/lib/navigation";

export function AIVision() {
  const nav = useNavigate();
  const providersQ = useQuery({ queryKey: ["llm-providers"], queryFn: listLLMProviders });

  if (providersQ.isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  const visionProviders = (providersQ.data || []).filter(
    (p) => p.modality === "vision" || p.modality === "multimodal" || (p.tags || []).includes("vision"),
  );

  return (
    <div className="space-y-5">
      <Button variant="ghost" size="sm" onClick={() => goBackOr(nav, "/ai")}>
        <ArrowLeft className="mr-1 h-4 w-4" /> 返回上一页
      </Button>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="inline-flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Eye className="h-5 w-5" /> 视觉理解
          </h1>
          <p className="text-sm text-muted-foreground">
            给可看图模型设置 modality=vision/multimodal 或 vision tag，供后续图文问答路由使用。
          </p>
        </div>
        <Button asChild size="sm">
          <Link to="/ai/providers">
            配置视觉 Provider
            <ArrowRight className="ml-1 h-4 w-4" />
          </Link>
        </Button>
      </div>

      <div className="rounded-md border bg-muted/20 p-3">
        <p className="text-xs text-muted-foreground">视觉/多模态 Provider</p>
        <p className="mt-1 text-xl font-semibold">{visionProviders.length}</p>
      </div>

      {visionProviders.length > 0 ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {visionProviders.map((provider) => (
            <Card key={provider.id}>
              <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-3">
                  <CardTitle className="text-base">{provider.name}</CardTitle>
                  <Badge variant={provider.has_api_key ? "success" : "outline"}>
                    {provider.has_api_key ? "可调用" : "缺少 Key"}
                  </Badge>
                </div>
                <CardDescription>{provider.default_model}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm text-muted-foreground">
                <div>模态：{provider.modality || "text"}</div>
                <div className="flex flex-wrap gap-2">
                  {(provider.tags || []).map((tag) => (
                    <Badge key={tag} variant="outline">
                      {tag}
                    </Badge>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="border-dashed">
          <CardHeader>
            <CardTitle className="text-base">暂无视觉 Provider</CardTitle>
            <CardDescription>编辑 Provider，把支持图像输入的模型标记为 vision 或 multimodal。</CardDescription>
          </CardHeader>
        </Card>
      )}
    </div>
  );
}
