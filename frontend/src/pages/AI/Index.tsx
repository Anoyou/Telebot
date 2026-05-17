import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Bot,
  BrainCircuit,
  CircleHelp,
  FileText,
  History,
  Image,
  Network,
  Package,
  Route,
  Search,
  Sparkles,
} from "lucide-react";

import { listCommandTemplates, listLLMProviders } from "@/api/commands";
import type { CommandTemplateOut, LLMProviderOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LLMProviders } from "@/pages/AI/LLMProviders";
import { RecentUsageContent } from "@/pages/AI/Usage";

type CapabilityState = "ready" | "partial" | "missing";

function capabilityBadge(state: CapabilityState) {
  if (state === "ready") return <Badge variant="success">已就绪</Badge>;
  if (state === "partial") return <Badge variant="warn">待完善</Badge>;
  return <Badge variant="outline">未配置</Badge>;
}

function providerLabel(p?: LLMProviderOut) {
  if (!p) return "未选择";
  return `${p.name} · ${p.default_model}`;
}

export function AIIndex() {
  const providersQ = useQuery({
    queryKey: ["llm-providers"],
    queryFn: listLLMProviders,
  });
  const templatesQ = useQuery({
    queryKey: ["cmd-tpl"],
    queryFn: listCommandTemplates,
  });

  const loading = providersQ.isLoading || templatesQ.isLoading;
  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  const providers = providersQ.data || [];
  const templates = templatesQ.data || [];
  const providerById = new Map(providers.map((p) => [p.id, p]));
  const aiTemplates = templates.filter((t) => t.type === "ai");
  const webSearchTemplates = aiTemplates.filter((t) => t.config?.web_search === true);
  const autoTemplates = aiTemplates.filter((t) => t.config?.routing_mode === "auto");
  const templateWithCustomOutput = aiTemplates.filter((t) => typeof t.config?.output_template === "string");
  const providerCount = providers.length;
  const readyCount = providers.filter((p) => p.has_api_key || p.provider === "ollama").length;
  const responsesCount = providers.filter((p) => p.api_format === "responses").length;
  const visionCount = providers.filter((p) => p.modality === "vision" || p.modality === "multimodal").length;

  const defaultTemplate = aiTemplates[0];
  const defaultProvider = defaultTemplate
    ? providerById.get(Number(defaultTemplate.config?.provider_id))
    : undefined;

  const hasProvider = readyCount > 0;
  const hasAI = aiTemplates.length > 0;
  const hasSearchProvider = responsesCount > 0;
  const capabilities = [
    {
      key: "chat",
      title: "聊天问答",
      icon: Bot,
      state: hasProvider && hasAI ? "ready" : hasProvider ? "partial" : "missing",
      desc: hasAI ? `已配置 ${aiTemplates.length} 条 AI 命令模板` : "需要至少一条 type=AI 的命令模板",
      action: "查看聊天入口",
      href: "/ai/chat",
    },
    {
      key: "route",
      title: "能力路由",
      icon: Route,
      state: autoTemplates.length > 0 ? "ready" : hasAI ? "partial" : "missing",
      desc: autoTemplates.length > 0 ? `${autoTemplates.length} 条模板启用自动路由` : "按 chat/code/math/vision 等标签自动选择模型",
      action: "查看路由概览",
      href: "/ai/routing",
    },
    {
      key: "search",
      title: "联网搜索",
      icon: Search,
      state: webSearchTemplates.length > 0 ? "ready" : hasSearchProvider ? "partial" : "missing",
      desc: hasSearchProvider
        ? `${webSearchTemplates.length} 条模板启用搜索；${responsesCount} 个 Responses provider 可用`
        : "需要 OpenAI Responses API provider",
      action: "查看搜索能力",
      href: "/ai/search",
    },
    {
      key: "vision",
      title: "视觉理解",
      icon: Sparkles,
      state: visionCount > 0 ? "ready" : "missing",
      desc: visionCount > 0 ? `${visionCount} 个视觉/多模态 provider` : "给支持看图的模型设置 modality=vision 或 multimodal",
      action: "查看视觉能力",
      href: "/ai/vision",
    },
    {
      key: "images",
      title: "图片生成",
      icon: Image,
      state: "partial",
      desc: "通过 codex_image 插件接入图片生成与账号级发送配置",
      action: "查看图片生成",
      href: "/ai/images",
    },
    {
      key: "output",
      title: "输出模板",
      icon: FileText,
      state: templateWithCustomOutput.length > 0 ? "ready" : hasAI ? "partial" : "missing",
      desc: templateWithCustomOutput.length > 0 ? `${templateWithCustomOutput.length} 条模板使用自定义输出` : "可配置 question/quoted/answer/sources 等占位符",
      action: "查看输出模板",
      href: "/ai/output",
    },
  ] as const;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">AI 模块</h1>
          <p className="text-sm text-muted-foreground">
            管理模型提供商、能力路由、联网搜索和 Telegram 输出体验。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to="/plugins/templates">
              <FileText className="mr-1 h-4 w-4" />
              命令模板
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/ai/help">
              <CircleHelp className="mr-1 h-4 w-4" />
              AI 帮助
            </Link>
          </Button>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        <Metric label="模型提供商" value={`${readyCount}/${providerCount}`} hint="已可调用 / 总数" />
        <Metric label="AI 命令模板" value={aiTemplates.length} hint={defaultTemplate ? `默认查看：${defaultTemplate.name}` : "尚未创建"} />
        <Metric label="联网搜索模板" value={webSearchTemplates.length} hint={`${responsesCount} 个 Responses provider`} />
        <Metric label="当前默认模型" value={providerLabel(defaultProvider)} hint="来自第一条 AI 模板" compact />
      </div>

      {providerCount === 0 ? (
        <Card className="border-dashed">
          <CardHeader>
            <CardTitle className="inline-flex items-center gap-2 text-base">
              <BrainCircuit className="h-4 w-4" /> 先接入一个模型提供商
            </CardTitle>
            <CardDescription>
              AI 模块的聊天、联网搜索、视觉理解都从 Provider 能力开始。
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

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview" className="gap-1.5">
            <Network className="h-4 w-4" /> 能力总览
          </TabsTrigger>
          <TabsTrigger value="providers" className="gap-1.5">
            <Package className="h-4 w-4" /> 模型提供商
          </TabsTrigger>
          <TabsTrigger value="usage" className="gap-1.5">
            <History className="h-4 w-4" /> 最近调用
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <div className="grid gap-3 lg:grid-cols-2">
            {capabilities.map((item) => (
              <Card key={item.key}>
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between gap-3">
                    <CardTitle className="inline-flex items-center gap-2 text-base">
                      <item.icon className="h-4 w-4" />
                      {item.title}
                    </CardTitle>
                    {capabilityBadge(item.state as CapabilityState)}
                  </div>
                  <CardDescription>{item.desc}</CardDescription>
                </CardHeader>
                <CardContent>
                  <Button asChild variant="outline" size="sm">
                    <Link to={item.href}>
                      {item.action}
                      <ArrowRight className="ml-1 h-4 w-4" />
                    </Link>
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">推荐落地顺序</CardTitle>
              <CardDescription>
                先让一条 AI 命令稳定可用，再打开搜索和自动路由。
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-2 text-sm text-muted-foreground md:grid-cols-3">
              <Step no="1" title="Provider" desc="添加 OpenAI/Anthropic/兼容接口，拉取并启用模型。" />
              <Step no="2" title="命令模板" desc="创建 ai 模板，绑定默认模型，启用到账号。" />
              <Step no="3" title="能力增强" desc="按需打开联网搜索、自动路由和自定义输出模板。" />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="providers" className="space-y-4">
          <div className="rounded-md border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
            已配置 {providerCount} 个模型提供商，其中 {readyCount} 个可调用。联网搜索需要 api_format=responses 的 OpenAI provider。
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

function Metric({
  label,
  value,
  hint,
  compact = false,
}: {
  label: string;
  value: string | number;
  hint: string;
  compact?: boolean;
}) {
  return (
    <div className="rounded-md border bg-muted/20 p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={compact ? "mt-1 truncate text-sm font-semibold" : "mt-1 text-xl font-semibold"}>{value}</p>
      <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

function Step({ no, title, desc }: { no: string; title: string; desc: string }) {
  return (
    <div className="rounded-md border bg-background p-3">
      <div className="flex items-center gap-2 font-medium text-foreground">
        <span className="flex h-6 w-6 items-center justify-center rounded-full border text-xs">{no}</span>
        {title}
      </div>
      <p className="mt-2 text-xs">{desc}</p>
    </div>
  );
}
