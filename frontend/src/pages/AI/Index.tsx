import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, Bot, CircleHelp, History, Package } from "lucide-react";

import { listLLMProviders } from "@/api/commands";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/misc";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LLMProviders } from "@/pages/AI/LLMProviders";
import { RecentUsageContent } from "@/pages/AI/Usage";

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
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">AI 中心</h1>
          <p className="text-sm text-muted-foreground">
            先配置模型提供商，再用最近调用确认 AI 命令是否真的跑起来。
          </p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link to="/ai/help">
            <CircleHelp className="mr-1 h-4 w-4" />
            AI 帮助
          </Link>
        </Button>
      </div>

      {providerCount === 0 ? (
        <Card className="border-dashed">
          <CardHeader>
            <CardTitle className="inline-flex items-center gap-2 text-base">
              <Bot className="h-4 w-4" /> 还没有可用模型
            </CardTitle>
            <CardDescription>
              先添加至少一个模型提供商并填写 API Key，AI 命令和调用记录视图才能工作。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild>
              <Link to="/ai/providers">
                去配置模型提供商
                <ArrowRight className="ml-1 h-4 w-4" />
              </Link>
            </Button>
          </CardContent>
        </Card>
      ) : null}

      <Tabs defaultValue="providers">
        <TabsList>
          <TabsTrigger value="providers" className="gap-1.5">
            <Package className="h-4 w-4" /> 模型提供商
          </TabsTrigger>
          <TabsTrigger value="usage" className="gap-1.5">
            <History className="h-4 w-4" /> 最近调用
          </TabsTrigger>
        </TabsList>
        <TabsContent value="providers" className="space-y-4">
          <div className="rounded-md border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
            已配置 {providerCount} 个模型提供商，其中 {readyCount} 个已填写 API Key。
          </div>
          <LLMProviders />
        </TabsContent>
        <TabsContent value="usage">
          <RecentUsageContent />
        </TabsContent>
      </Tabs>
    </div>
  );
}
