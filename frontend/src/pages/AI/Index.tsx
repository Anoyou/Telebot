import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, Bot, GitBranch, History, Package } from "lucide-react";

import { listLLMProviders } from "@/api/commands";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function AIIndex() {
  const providersQ = useQuery({
    queryKey: ["llm-providers"],
    queryFn: listLLMProviders,
  });

  if (providersQ.isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  const providers = providersQ.data || [];
  const providerCount = providers.length;
  const readyCount = providers.filter((p) => p.has_api_key).length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">AI</h1>
        <p className="text-sm text-muted-foreground">AI 顶级整合入口：Provider、路由策略、最近调用。</p>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="inline-flex items-center gap-2 text-base">
              <Package className="h-4 w-4" /> Provider
            </CardTitle>
            <CardDescription>模型提供商配置状态</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-2xl font-semibold">{providerCount}</div>
            <div className="text-xs text-muted-foreground">已配置 API Key：{readyCount}</div>
            <Button size="sm" variant="outline" asChild>
              <Link to="/ai/providers">
                管理 Provider <ArrowRight className="ml-1 h-4 w-4" />
              </Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="inline-flex items-center gap-2 text-base">
              <GitBranch className="h-4 w-4" /> 路由策略
            </CardTitle>
            <CardDescription>查看规则命中与推荐配置</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Badge variant="secondary">复用现有 AISettings 内容</Badge>
            <Button size="sm" variant="outline" asChild>
              <Link to="/ai/routing">
                打开路由策略 <ArrowRight className="ml-1 h-4 w-4" />
              </Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="inline-flex items-center gap-2 text-base">
              <History className="h-4 w-4" /> 最近调用
            </CardTitle>
            <CardDescription>最小化 usage 记录视图</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Button size="sm" variant="outline" asChild>
              <Link to="/ai/usage">
                打开最近调用 <ArrowRight className="ml-1 h-4 w-4" />
              </Link>
            </Button>
          </CardContent>
        </Card>
      </div>

      {providerCount === 0 ? (
        <Card className="border-dashed">
          <CardHeader>
            <CardTitle className="inline-flex items-center gap-2 text-base">
              <Bot className="h-4 w-4" /> 还没有可用模型
            </CardTitle>
            <CardDescription>
              先添加至少一个 Provider 并填写 API Key，AI 命令和 usage 视图才能工作。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild>
              <Link to="/ai/providers">
                去 /ai/providers 配置
                <ArrowRight className="ml-1 h-4 w-4" />
              </Link>
            </Button>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
