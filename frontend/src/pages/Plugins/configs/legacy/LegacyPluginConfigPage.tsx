// W1 迁移后的对照入口：五个插件的旧专属配置页只保留一个版本（下一版删除）。
// 新版统一走 GenericPluginConfigPage（x-ui-mode=rules / x-secret-list / 通用表单）。
import { lazy, Suspense } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { Info } from "lucide-react";

import { Spinner } from "@/components/ui/misc";
import { isLegacyConfigPageKey } from "@/pages/Plugins/_shared/featureConfig";

const PAGES = {
  auto_reply: lazy(() => import("./AutoReply").then((m) => ({ default: m.AutoReplyConfig }))),
  autorepeat: lazy(() => import("./Autorepeat").then((m) => ({ default: m.AutorepeatConfig }))),
  chatgpt_image: lazy(() => import("./ChatGPTImageConfig").then((m) => ({ default: m.ChatGPTImageConfigPage }))),
  codex_image: lazy(() => import("./CodexImageConfig").then((m) => ({ default: m.CodexImageConfigPage }))),
  game24: lazy(() => import("./Game24Config").then((m) => ({ default: m.Game24ConfigPage }))),
} as const;

export function LegacyPluginConfigPage() {
  const params = useParams();
  const key = params.featureKey ?? "";
  if (!isLegacyConfigPageKey(key)) {
    return <Navigate to={`/accounts/${params.aid}/features/${encodeURIComponent(key)}`} replace />;
  }
  const Page = PAGES[key as keyof typeof PAGES];
  return (
    <div className="space-y-3">
      <div
        className="flex items-start gap-2 rounded-md border border-info/30 bg-info/5 px-3 py-2 text-xs text-muted-foreground"
        data-testid="legacy-config-banner"
      >
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-info" />
        <div>
          这是旧版专属配置页，只保留一个版本用于对照，下一版删除。
          <Link className="ml-1 font-medium text-primary underline-offset-2 hover:underline" to={`/accounts/${params.aid}/features/${key}?legacy=0`}>
            打开新版通用配置页
          </Link>
        </div>
      </div>
      <Suspense fallback={<div className="flex h-24 items-center justify-center"><Spinner className="text-primary" /></div>}>
        <Page />
      </Suspense>
    </div>
  );
}
