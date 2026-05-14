import { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";

/** 顶部"返回 + 标题"行，4 个 feature page 共用。 */
export function RulePageHeader({
  title,
  backLabel = "返回账号",
  backHref,
}: {
  title: string;
  backLabel?: string;
  backHref?: string;
}) {
  const nav = useNavigate();
  return (
    <div className="flex flex-wrap items-center gap-3">
      {backHref ? (
        <Button variant="ghost" size="sm" onClick={() => nav(backHref)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> {backLabel}
        </Button>
      ) : null}
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
    </div>
  );
}

/** "功能总开关" Card，AutoReply/Autorepeat/Forward 共用。 */
export function RuleFeatureToggleCard({
  enabled,
  onToggle,
  description = "关闭后所有规则都不会触发；启用即生效",
}: {
  enabled: boolean;
  onToggle: (next: boolean) => void;
  description?: string;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base">功能总开关</CardTitle>
            <CardDescription>{description}</CardDescription>
          </div>
          <Switch checked={enabled} onCheckedChange={onToggle} />
        </div>
      </CardHeader>
    </Card>
  );
}

/** 提示条容器（统一 alert-info 样式 + space-y-1）。 */
export function RuleInfoBox({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-md border px-3 py-2 text-xs alert-info space-y-1">
      {children}
    </div>
  );
}

/** Label + 子内容；4 个文件原本各自定义一份同样的 Field。 */
export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      {children}
    </div>
  );
}
