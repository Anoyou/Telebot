import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function CommandsTab({ aid }: { aid: number }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">命令管理已迁移</CardTitle>
        <CardDescription>
          账号级命令启停正在收敛到模板中心，当前页仅保留过渡入口。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          建议改用新入口管理：统一维护模板，再按账号维度筛选与启停。
        </p>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to={`/plugins/templates?account=${aid}`}>前往模板中心（按账号筛选）</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to={`/accounts/${aid}?tab=overview`}>返回概览查看迁移入口</Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
