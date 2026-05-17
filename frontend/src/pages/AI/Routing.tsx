import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, Network, Route } from "lucide-react";
import { toast } from "sonner";

import { listCommandTemplates, listLLMProviders, patchCommandTemplate } from "@/api/commands";
import type { LLMProviderOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { AIPageShell, FieldKV } from "@/pages/AI/_shared";

export function AIRouting() {
  const qc = useQueryClient();
  const providersQ = useQuery({ queryKey: ["llm-providers"], queryFn: listLLMProviders });
  const templatesQ = useQuery({ queryKey: ["cmd-tpl"], queryFn: listCommandTemplates });

  const updateRouteMut = useMutation({
    mutationFn: async ({
      id,
      mode,
      fallbackProviderId,
    }: {
      id: number;
      mode?: "fixed" | "auto";
      fallbackProviderId?: number;
    }) => {
      const template = (templatesQ.data || []).find((t) => t.id === id);
      if (!template) return null;
      const config: Record<string, unknown> = { ...template.config };
      if (mode) config.routing_mode = mode;
      if (fallbackProviderId) config.routing_fallback_provider_id = fallbackProviderId;
      return patchCommandTemplate(id, { config });
    },
    onSuccess: async () => {
      toast.success("能力路由设置已保存");
      await qc.invalidateQueries({ queryKey: ["cmd-tpl"] });
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail || err?.message || "保存失败"),
  });

  if (providersQ.isLoading || templatesQ.isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  const providers = providersQ.data || [];
  const aiTemplates = (templatesQ.data || []).filter((t) => t.type === "ai");
  const autoTemplates = aiTemplates.filter((t) => t.config?.routing_mode === "auto");
  const tags = new Map<string, number>();
  for (const provider of providers) {
    for (const tag of provider.tags || []) tags.set(tag, (tags.get(tag) || 0) + 1);
  }

  return (
    <AIPageShell
      title="能力路由"
      description="根据 Provider 的 modality、tags 与成本档位，让指定 AI 命令从固定模型切换到自动选择。"
      actions={
        <Button asChild size="sm">
          <Link to="/ai/providers">
            调整 Provider 能力
            <ArrowRight className="ml-1 h-4 w-4" />
          </Link>
        </Button>
      }
    >
      <div className="grid gap-3 md:grid-cols-3">
        <FieldKV label="Provider" value={providers.length} />
        <FieldKV label="已标记能力标签" value={tags.size} />
        <FieldKV label="自动路由模板" value={`${autoTemplates.length}/${aiTemplates.length}`} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="inline-flex items-center gap-2 text-base">
            <Route className="h-4 w-4" /> 路由命令
          </CardTitle>
          <CardDescription>固定模式使用命令里的 Provider；自动模式会按消息内容与 Provider 能力选择，失败时走兜底 Provider。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {aiTemplates.length > 0 ? (
            aiTemplates.map((template) => {
              const mode = template.config?.routing_mode === "auto" ? "auto" : "fixed";
              const fallbackId = Number(template.config?.routing_fallback_provider_id || template.config?.provider_id || 0);
              return (
                <div key={template.id} className="rounded-md border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <code>,{template.name}</code>
                        <Badge variant={mode === "auto" ? "success" : "outline"}>{mode === "auto" ? "自动路由" : "固定模型"}</Badge>
                      </div>
                      <p className="mt-1 text-sm text-muted-foreground">{template.description || "未填写说明"}</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Select
                        value={String(fallbackId)}
                        className="w-44"
                        disabled={updateRouteMut.isPending || providers.length === 0}
                        onChange={(e) =>
                          updateRouteMut.mutate({
                            id: template.id,
                            fallbackProviderId: Number(e.target.value),
                          })
                        }
                      >
                        {providers.map((provider) => (
                          <option key={provider.id} value={provider.id}>
                            {provider.name}
                          </option>
                        ))}
                      </Select>
                      <Button
                        size="sm"
                        variant={mode === "auto" ? "outline" : "default"}
                        disabled={updateRouteMut.isPending || providers.length === 0}
                        onClick={() =>
                          updateRouteMut.mutate({
                            id: template.id,
                            mode: mode === "auto" ? "fixed" : "auto",
                            fallbackProviderId: fallbackId || Number(template.config?.provider_id || providers[0]?.id),
                          })
                        }
                      >
                        {mode === "auto" ? "切回固定" : "启用自动"}
                      </Button>
                    </div>
                  </div>
                </div>
              );
            })
          ) : (
            <p className="text-sm text-muted-foreground">暂无 AI 命令模板，请先在「自定义命令」里创建 type=AI 模板。</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="inline-flex items-center gap-2 text-base">
            <Network className="h-4 w-4" /> 标签覆盖
          </CardTitle>
          <CardDescription>这些 tags 会参与 chat/code/math/vision 等自动选择。</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {tags.size > 0 ? (
            Array.from(tags.entries()).map(([tag, count]) => (
              <Badge key={tag} variant="secondary">
                {tag} · {count}
              </Badge>
            ))
          ) : (
            <span className="text-sm text-muted-foreground">暂无标签，请在 Provider 里补充 tags。</span>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-3 lg:grid-cols-2">
        {providers.map((provider) => (
          <ProviderCard key={provider.id} provider={provider} />
        ))}
      </div>
    </AIPageShell>
  );
}

function ProviderCard({ provider }: { provider: LLMProviderOut }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <CardTitle className="text-base">{provider.name}</CardTitle>
          <Badge variant={provider.has_api_key || provider.provider === "ollama" ? "success" : "outline"}>
            {provider.has_api_key || provider.provider === "ollama" ? "可调用" : "未配置 Key"}
          </Badge>
        </div>
        <CardDescription>{provider.default_model}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm text-muted-foreground">
        <div className="grid gap-2 sm:grid-cols-3">
          <span>协议：{provider.api_format || "chat_completions"}</span>
          <span>模态：{provider.modality || "text"}</span>
          <span>成本档：{provider.cost_tier || 2}</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {(provider.tags || []).length > 0 ? (
            provider.tags?.map((tag) => (
              <Badge key={tag} variant="outline">
                {tag}
              </Badge>
            ))
          ) : (
            <span>未设置 tags</span>
          )}
        </div>
        {provider.notes ? <p>{provider.notes}</p> : null}
      </CardContent>
    </Card>
  );
}
