// 共用 TODO 占位组件：feature 配置页未实现时复用
import { ArrowLeft } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface TodoPageProps {
  title: string;
  description: string;
}

export function FeatureTodoPage({ title, description }: TodoPageProps) {
  const nav = useNavigate();
  const { aid } = useParams();
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => nav(`/accounts/${aid}`)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回账号
        </Button>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">即将上线</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          MVP 阶段仅完成自动回复的完整可视化配置；该功能页面将在下一迭代实现。
          可以通过 API 直连完成基础规则的写入。
        </CardContent>
      </Card>
    </div>
  );
}
