import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Search as SearchIcon } from "lucide-react";
import { toast } from "sonner";

import { listCommandTemplates, listLLMProviders, patchCommandTemplate } from "@/api/commands";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { AIPageShell, EmptyState, FieldKV } from "@/pages/AI/_shared";

type SearchContextSize = "low" | "medium" | "high";

export function AISearch() {
  const qc = useQueryClient();
  const providersQ = useQuery({ queryKey: ["llm-providers"], queryFn: listLLMProviders });
  const templatesQ = useQuery({ queryKey: ["cmd-tpl"], queryFn: listCommandTemplates });

  const toggleMut = useMutation({
    mutationFn: async ({ id, enabled, contextSize }: { id: number; enabled: boolean; contextSize?: SearchContextSize }) => {
      const template = (templatesQ.data || []).find((t) => t.id === id);
      if (!template) return null;
      const config: Record<string, unknown> = { ...template.config };
      if (enabled) {
        config.web_search = true;
        config.web_search_context_size = contextSize || config.web_search_context_size || "medium";
      } else {
        delete config.web_search;
        delete config.web_search_context_size;
      }
      return patchCommandTemplate(id, { config });
    },
    onSuccess: async () => {
      toast.success("联网搜索设置已保存");
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

  const responsesProviders = (providersQ.data || []).filter((p) => p.api_format === "responses");
  const aiTemplates = (templatesQ.data || []).filter((t) => t.type === "ai");
  const searchTemplates = aiTemplates.filter((t) => t.config?.web_search === true);

  return (
    <AIPageShell
      title="联网搜索"
      description="通过 OpenAI Responses API 的 web_search 工具，让指定 AI 命令可以查询最新信息。"
      actions={
        <>
          <Button asChild variant="outline" size="sm">
            <Link to="/ai/providers">配置 Responses Provider</Link>
          </Button>
          <Button asChild size="sm">
            <Link to="/plugins/templates">
              完整编辑命令
              <ArrowRight className="ml-1 h-4 w-4" />
            </Link>
          </Button>
        </>
      }
    >
      <div className="grid gap-3 md:grid-cols-2">
        <FieldKV label="Responses Provider" value={responsesProviders.length} />
        <FieldKV label="已启用搜索模板" value={`${searchTemplates.length}/${aiTemplates.length}`} />
      </div>

      <Section title="Responses Provider" desc="只有 api_format=responses 的 Provider 会传递 web_search 工具。">
        {responsesProviders.length > 0 ? (
          responsesProviders.map((provider) => (
            <div key={provider.id} className="rounded-md border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="font-medium">{provider.name}</div>
                <Badge variant={provider.has_api_key ? "success" : "outline"}>
                  {provider.has_api_key ? "已配置 Key" : "缺少 Key"}
                </Badge>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">{provider.default_model}</p>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">暂无 Responses Provider，请在 Provider 页面把协议切到 Responses。</p>
        )}
      </Section>

      <Section title="搜索命令" desc="在这里直接决定哪条 AI 命令启用联网搜索，以及搜索上下文强度。">
        {aiTemplates.length > 0 ? (
          aiTemplates.map((template) => {
            const enabled = template.config?.web_search === true;
            const contextSize = (template.config?.web_search_context_size || "medium") as SearchContextSize;
            return (
              <div key={template.id} className="rounded-md border p-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <code>,{template.name}</code>
                      <Badge variant={enabled ? "success" : "outline"}>{enabled ? "已启用" : "未启用"}</Badge>
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">{template.description || "未填写说明"}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Select
                      value={contextSize}
                      className="w-28"
                      disabled={!enabled || toggleMut.isPending}
                      onChange={(e) =>
                        toggleMut.mutate({
                          id: template.id,
                          enabled: true,
                          contextSize: e.target.value as SearchContextSize,
                        })
                      }
                    >
                      <option value="low">low</option>
                      <option value="medium">medium</option>
                      <option value="high">high</option>
                    </Select>
                    <Button
                      size="sm"
                      variant={enabled ? "outline" : "default"}
                      disabled={toggleMut.isPending || responsesProviders.length === 0}
                      onClick={() =>
                        toggleMut.mutate({
                          id: template.id,
                          enabled: !enabled,
                          contextSize,
                        })
                      }
                    >
                      <SearchIcon className="mr-1 h-4 w-4" />
                      {enabled ? "关闭搜索" : "启用搜索"}
                    </Button>
                  </div>
                </div>
              </div>
            );
          })
        ) : (
          <EmptyState
            title="还没有 AI 命令模板"
            description="先创建一条 type=AI 的命令模板，再回来把它作为搜索问答入口。"
            actionHref="/plugins/templates"
            actionLabel="创建 AI 命令模板"
          />
        )}
      </Section>
    </AIPageShell>
  );
}

function Section({ title, desc, children }: { title: string; desc: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{desc}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">{children}</CardContent>
    </Card>
  );
}
