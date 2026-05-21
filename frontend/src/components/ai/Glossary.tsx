import { ListChecks } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { MetaBadge } from "@/components/ui/meta-badge";

export function Glossary({ defaultOpen = false }: { defaultOpen?: boolean }) {
  return (
    <details className="group" open={defaultOpen}>
      <summary className="cursor-pointer list-none">
        <Card className="transition-colors group-open:border-primary/40">
          <CardHeader className="pb-3">
            <CardTitle className="inline-flex items-center gap-2 text-base">
              <ListChecks className="h-4 w-4" />
              术语速查
            </CardTitle>
            <CardDescription>模态、标签和成本档会一起影响自动路由如何挑模型。</CardDescription>
          </CardHeader>
          <CardContent className="hidden space-y-4 text-sm group-open:block">
            <section>
              <h4 className="mb-1.5 font-semibold">模态 modality</h4>
              <ul className="space-y-1 text-xs text-muted-foreground">
                <li><MetaBadge className="mr-1.5">text</MetaBadge>纯文本输入输出。</li>
                <li><MetaBadge className="mr-1.5">vision</MetaBadge>支持图文输入到文本输出。</li>
                <li><MetaBadge className="mr-1.5">audio</MetaBadge>支持语音转写、合成或实时语音。</li>
                <li><MetaBadge className="mr-1.5">multimodal</MetaBadge>支持图、音、视频等多种输入。</li>
              </ul>
            </section>

            <section>
              <h4 className="mb-1.5 font-semibold">路由标签 tags</h4>
              <div className="flex flex-wrap gap-2 text-xs">
                <TagDef tag="chat" desc="通用问答" />
                <TagDef tag="code" desc="代码生成和调试" />
                <TagDef tag="math" desc="数学推导" />
                <TagDef tag="translate" desc="多语种翻译" />
                <TagDef tag="vision" desc="看图、OCR、图像理解" />
                <TagDef tag="long_context" desc="长上下文" />
                <TagDef tag="reason" desc="复杂推理" />
                <TagDef tag="smart" desc="高质量主力回答" />
                <TagDef tag="cheap" desc="低成本量产" />
                <TagDef tag="fast" desc="低延迟优先" />
                <TagDef tag="classify" desc="路由分类器" />
              </div>
            </section>

            <section>
              <h4 className="mb-1.5 font-semibold">推理成本档 cost_tier</h4>
              <ul className="space-y-1 text-xs text-muted-foreground">
                <li><MetaBadge className="mr-1.5">tier 1</MetaBadge>便宜量产档，适合 chat、classify、translate。</li>
                <li><MetaBadge className="mr-1.5">tier 2</MetaBadge>默认中档，适合大多数场景。</li>
                <li><MetaBadge className="mr-1.5">tier 3</MetaBadge>旗舰档，适合 reason、smart、复杂代码与长文。</li>
              </ul>
            </section>
          </CardContent>
        </Card>
      </summary>
    </details>
  );
}

function TagDef({ tag, desc }: { tag: string; desc: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border bg-background px-2 py-1">
      <MetaBadge mono>{tag}</MetaBadge>
      <span className="text-muted-foreground">{desc}</span>
    </span>
  );
}
