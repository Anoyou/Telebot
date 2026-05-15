import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import type { ReactNode } from "react";
import { ArrowLeft, ArrowRight, History } from "lucide-react";

import { listRecentLLMUsage } from "@/api/llmUsage";
import { listLLMProviders } from "@/api/commands";
import { getErrMsg } from "@/lib/api";
import { Spinner } from "@/components/ui/misc";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { goBackOr } from "@/lib/navigation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export function AIUsage() {
  const nav = useNavigate();
  return (
    <AIUsageShell onBack={() => goBackOr(nav, "/ai")}>
      <RecentUsageContent />
    </AIUsageShell>
  );
}

export function RecentUsageContent() {
  const providersQ = useQuery({
    queryKey: ["llm-providers"],
    queryFn: listLLMProviders,
  });

  const providerCount = providersQ.data?.length ?? 0;
  const hasProviders = providerCount > 0;
  const usageQ = useQuery({
    queryKey: ["llm-usage", "recent"],
    queryFn: () => listRecentLLMUsage(20),
    retry: false,
    enabled: hasProviders,
  });

  if (providersQ.isLoading || (hasProviders && usageQ.isLoading)) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  if (providersQ.isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="inline-flex items-center gap-2">
            <History className="h-4 w-4" /> 最近调用
          </CardTitle>
          <CardDescription>暂时无法读取模型提供商：{getErrMsg(providersQ.error)}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (providerCount === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>最近调用</CardTitle>
          <CardDescription>先配置至少一个模型提供商，才会产生可查看的调用记录。</CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild>
            <Link to="/ai/providers">
              前往配置模型提供商
              <ArrowRight className="ml-1 h-4 w-4" />
            </Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (usageQ.isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="inline-flex items-center gap-2">
            <History className="h-4 w-4" /> 最近调用
          </CardTitle>
          <CardDescription>暂时无法读取调用记录：{getErrMsg(usageQ.error)}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const rows = usageQ.data || [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="inline-flex items-center gap-2">
          <History className="h-4 w-4" /> 最近调用
        </CardTitle>
        <CardDescription>展示最近 20 条 LLM 调用记录（最小可用视图）。</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="rounded-md border border-dashed py-8 text-center text-sm text-muted-foreground">
            暂无调用记录。触发一次 AI 命令后再回来查看。
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>来源</TableHead>
                <TableHead>模型提供商</TableHead>
                <TableHead>模型</TableHead>
                <TableHead>Token 数</TableHead>
                <TableHead>耗时</TableHead>
                <TableHead>状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="text-xs text-muted-foreground">{new Date(r.created_at).toLocaleString()}</TableCell>
                  <TableCell className="font-mono text-xs">{r.source || "-"}</TableCell>
                  <TableCell>{r.provider_name || (r.provider_id ? `#${r.provider_id}` : "-")}</TableCell>
                  <TableCell className="font-mono text-xs">{r.model || "-"}</TableCell>
                  <TableCell>{(r.input_tokens || 0) + (r.output_tokens || 0)}</TableCell>
                  <TableCell>{r.latency_ms != null ? `${r.latency_ms}ms` : "-"}</TableCell>
                  <TableCell>
                    <Badge variant={r.success ? "success" : "warn"}>
                      {r.success ? (r.used_fallback ? "成功 · fallback" : "成功") : (r.error_type || "失败")}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function AIUsageShell({
  children,
  onBack,
}: {
  children: ReactNode;
  onBack: () => void;
}) {
  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ArrowLeft className="mr-1 h-4 w-4" /> 返回上一页
      </Button>
      {children}
    </div>
  );
}
