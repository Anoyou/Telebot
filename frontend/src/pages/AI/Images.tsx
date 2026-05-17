import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, Image as ImageIcon, Settings2 } from "lucide-react";

import { listAccounts } from "@/api/accounts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import { goBackOr } from "@/lib/navigation";

export function AIImages() {
  const nav = useNavigate();
  const accountsQ = useQuery({ queryKey: ["accounts"], queryFn: listAccounts });

  if (accountsQ.isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  const accounts = accountsQ.data || [];

  return (
    <div className="space-y-5">
      <Button variant="ghost" size="sm" onClick={() => goBackOr(nav, "/ai")}>
        <ArrowLeft className="mr-1 h-4 w-4" /> 返回上一页
      </Button>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="inline-flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <ImageIcon className="h-5 w-5" /> 图片生成
          </h1>
          <p className="text-sm text-muted-foreground">
            codex_image 插件负责图片生成与发送，这里聚合账号级配置入口。
          </p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link to="/plugins">
            <Settings2 className="mr-1 h-4 w-4" /> 插件中心
          </Link>
        </Button>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-base">codex_image 插件</CardTitle>
            <Badge variant="warn">实验能力</Badge>
          </div>
          <CardDescription>
            为账号开启插件后，可配置触发命令、图片模型、尺寸、超时与失败提示。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {accounts.length > 0 ? (
            accounts.map((account) => (
              <div key={account.id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3">
                <div>
                  <div className="font-medium">{account.display_name || account.phone || `账号 #${account.id}`}</div>
                  <p className="text-sm text-muted-foreground">账号级 codex_image 配置入口</p>
                </div>
                <Button asChild size="sm">
                  <Link to={`/accounts/${account.id}/features/codex_image`}>
                    配置
                    <ArrowRight className="ml-1 h-4 w-4" />
                  </Link>
                </Button>
              </div>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">暂无账号。添加账号后即可进入 codex_image 配置。</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
