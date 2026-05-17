import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bot, FileText, Package } from "lucide-react";

import { listCommandTemplates, listLLMProviders } from "@/api/commands";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AIPageShell, EmptyState, FieldKV } from "@/pages/AI/_shared";

export function AIChat() {
  const providersQ = useQuery({ queryKey: ["llm-providers"], queryFn: listLLMProviders });
  const templatesQ = useQuery({ queryKey: ["cmd-tpl"], queryFn: listCommandTemplates });

  if (providersQ.isLoading || templatesQ.isLoading) {
    return <div className="flex h-40 items-center justify-center"><Spinner className="text-primary" /></div>;
  }

  const providers = providersQ.data || [];
  const providerById = new Map(providers.map((p) => [p.id, p]));
  const aiTemplates = (templatesQ.data || []).filter((t) => t.type === "ai");
  const readyProviders = providers.filter((p) => p.has_api_key || p.provider === "ollama");

  return (
    <AIPageShell
      title="聊天问答"
      description="管理 Telegram 里的基础 AI 问答命令，默认仍落到 AI 类型命令模板。"
      actions={
        <>
          <Button asChild variant="outline" size="sm"><Link to="/ai/providers"><Package className="mr-1 h-4 w-4" />模型提供商</Link></Button>
          <Button asChild size="sm"><Link to="/plugins/templates"><FileText className="mr-1 h-4 w-4" />编辑命令模板</Link></Button>
        </>
      }
    >
      <div className="grid gap-3 md:grid-cols-3">
        <FieldKV label="可调用 Provider" value={`${readyProviders.length}/${providers.length}`} />
        <FieldKV label="AI 命令模板" value={aiTemplates.length} />
        <FieldKV label="推荐命令" value={<code>,ai 问题</code>} />
      </div>

      {aiTemplates.length === 0 ? (
        <EmptyState
          title="还没有聊天问答命令"
          description="先创建一条 type=AI 的命令模板，例如命名为 ai，再到账号详情启用。"
          actionHref="/plugins/templates"
          actionLabel="创建 AI 命令模板"
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="inline-flex items-center gap-2 text-base"><Bot className="h-4 w-4" /> 问答命令</CardTitle>
            <CardDescription>这里列出所有 AI 类型模板。基础聊天能力从这些模板触发。</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>命令</TableHead>
                  <TableHead>模型</TableHead>
                  <TableHead>引用回复</TableHead>
                  <TableHead>发送方式</TableHead>
                  <TableHead>能力</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {aiTemplates.map((t) => {
                  const p = providerById.get(Number(t.config.provider_id));
                  return (
                    <TableRow key={t.id}>
                      <TableCell><code>,{t.name}</code></TableCell>
                      <TableCell>{p ? `${p.name} · ${t.config.model || p.default_model}` : "Provider 缺失"}</TableCell>
                      <TableCell>{t.config.quote_replied === false ? "否" : "是"}</TableCell>
                      <TableCell>{t.config.send_mode === "send_new" ? "发新消息" : "编辑原消息"}</TableCell>
                      <TableCell className="space-x-1">
                        {t.config.routing_mode === "auto" ? <Badge variant="outline">auto</Badge> : null}
                        {t.config.web_search ? <Badge variant="outline">联网</Badge> : null}
                        {typeof t.config.output_template === "string" ? <Badge variant="outline">模板</Badge> : null}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </AIPageShell>
  );
}
