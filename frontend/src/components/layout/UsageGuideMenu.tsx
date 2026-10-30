// 使用指南面板：结构与 ChangelogMenu 保持一致，内容走 runtime-content 通道，
// 避免把完整 Markdown 作为 JavaScript 字符串参与 Rollup 解析和压缩。
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Button } from "@/components/ui/button";

const USAGE_GUIDE_URL = "/runtime-content/docs/USAGE-GUIDE.md";

export default function UsageGuideMenu() {
  const guideQ = useQuery({
    queryKey: ["runtime-content", "usage-guide"],
    queryFn: async ({ signal }) => {
      const response = await fetch(USAGE_GUIDE_URL, { cache: "no-cache", signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.text();
    },
    staleTime: 0,
    refetchOnWindowFocus: true,
  });
  const content = useMemo(() => guideQ.data ?? "", [guideQ.data]);
  return (
    <>
      <div className="border-b px-4 py-3">
        <div className="text-base font-semibold">使用指南</div>
        <div className="mt-1 text-sm text-muted-foreground">
          安装、界面导览、Telegram 通道、Agent、密钥池与排障入口。
        </div>
      </div>
      <div className="space-y-5 p-4">
        {content ? (
          <article className="prose prose-sm max-w-none text-sm text-muted-foreground dark:prose-invert">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </article>
        ) : guideQ.isError ? (
          <div className="space-y-3 text-sm text-muted-foreground">
            <p>使用指南读取失败：{(guideQ.error as Error)?.message || "网络错误"}</p>
            <Button size="sm" variant="outline" onClick={() => void guideQ.refetch()}>
              <RefreshCw className="mr-1.5 h-4 w-4" />
              重新读取
            </Button>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">正在读取使用指南…</p>
        )}
      </div>
    </>
  );
}
