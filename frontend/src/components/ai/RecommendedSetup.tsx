import { Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CommandBadge } from "@/components/CommandBadge";

const rows: Array<{
  name: string;
  protocol: string;
  modality: string;
  tags: string[];
  tier: number;
  role: string;
  note: string;
}> = [
  {
    name: "Claude Opus 4.7",
    protocol: "anthropic",
    modality: "vision",
    tags: ["smart", "reason", "code", "long_context", "vision"],
    tier: 3,
    role: "答主力",
    note: "代码、长文和复杂推理优先。",
  },
  {
    name: "GPT 5.5",
    protocol: "openai",
    modality: "multimodal",
    tags: ["smart", "reason", "vision"],
    tier: 3,
    role: "通用兜底",
    note: "全模态备份，旗舰模型不可用时顶上。",
  },
  {
    name: "GLM 4.7",
    protocol: "openai compatible",
    modality: "text",
    tags: ["chat", "code", "classify", "cheap"],
    tier: 1,
    role: "中文闲聊",
    note: "适合短问短答和 classifier。",
  },
  {
    name: "Mimo V2.5 Pro",
    protocol: "openai compatible",
    modality: "text",
    tags: ["chat", "translate", "cheap", "fast"],
    tier: 1,
    role: "翻译量产",
    note: "低延迟翻译和短文闲聊。",
  },
];

export function RecommendedSetup({ cmdPrefix = ",", defaultOpen = false }: { cmdPrefix?: string; defaultOpen?: boolean }) {
  return (
    <details className="group" open={defaultOpen}>
      <summary className="cursor-pointer list-none">
        <Card className="transition-colors group-open:border-primary/40">
          <CardHeader className="pb-3">
            <CardTitle className="inline-flex items-center gap-2 text-base">
              <Sparkles className="h-4 w-4" />
              配置示例
            </CardTitle>
            <CardDescription>
              一个 auto 指令搭配几条 fixed 指令，能兼顾省 token、答主力和手动覆盖。
            </CardDescription>
          </CardHeader>
          <CardContent className="hidden space-y-4 group-open:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>模型</TableHead>
                  <TableHead>协议</TableHead>
                  <TableHead>模态</TableHead>
                  <TableHead>标签</TableHead>
                  <TableHead>档位</TableHead>
                  <TableHead>定位</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.name}>
                    <TableCell className="font-medium">{r.name}</TableCell>
                    <TableCell className="font-mono text-xs">{r.protocol}</TableCell>
                    <TableCell><Badge variant="outline">{r.modality}</Badge></TableCell>
                    <TableCell className="space-x-1">
                      {r.tags.map((t) => <Badge key={t} variant="outline" className="text-xs">{t}</Badge>)}
                    </TableCell>
                    <TableCell><Badge variant="secondary">{r.tier}</Badge></TableCell>
                    <TableCell className="text-xs">
                      <div className="font-medium">{r.role}</div>
                      <div className="text-muted-foreground">{r.note}</div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            <div className="rounded-md border px-3 py-2 text-xs alert-info">
              <p className="font-semibold">推荐落地组合</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                <li>
                  建一条 <CommandBadge>{cmdPrefix}ai</CommandBadge> 模板设 auto：默认兜底用旗舰模型，classifier 用便宜模型。
                </li>
                <li>
                  同一条 <CommandBadge>{cmdPrefix}ai</CommandBadge> 也能用二级指令：<CommandBadge>{cmdPrefix}ai search</CommandBadge>、<CommandBadge>{cmdPrefix}ai image</CommandBadge>。
                </li>
                <li>
                  再建 <CommandBadge>{cmdPrefix}opus</CommandBadge>、<CommandBadge>{cmdPrefix}gpt</CommandBadge>、<CommandBadge>{cmdPrefix}search</CommandBadge>、<CommandBadge>{cmdPrefix}image</CommandBadge> 这类 fixed/direct 模板做手动覆盖。
                </li>
                <li>图片生成可选 image 模式 + codex_image；若 Provider 支持原生生图，也可选 LLM Provider 原生生图。</li>
                <li>视觉场景会根据被回复消息和模型 modality 自动进入 vision 或 multimodal 候选池。</li>
              </ul>
            </div>
          </CardContent>
        </Card>
      </summary>
    </details>
  );
}
