// 顶层「AI 设置」页：把 LLM Provider 从系统设置里提出来，独立成页。
// 顶部展示路由原理 + 模型推荐配置；下半部是 LLMProviders 子组件做增删改查。
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, ListChecks, Sparkles } from "lucide-react";

import { getSystemSettings } from "@/api/system";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function AISettings() {
  const [tab, setTab] = useState<"guide" | "glossary" | "recommend">("guide");
  // 实时拉系统命令前缀，渲染时用——避免硬编码 `,` 与系统设置里的真实前缀不同步
  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const cmdPrefix = settingsQ.data?.command_prefix || ",";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">AI 帮助</h1>
        <p className="text-sm text-muted-foreground">
          了解 AI 命令工作原理、术语定义和推荐配置，帮助你更快完成设置。
        </p>
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList>
          <TabsTrigger value="guide" className="gap-1.5">
            <BookOpen className="h-4 w-4" /> 工作原理
          </TabsTrigger>
          <TabsTrigger value="glossary" className="gap-1.5">
            <ListChecks className="h-4 w-4" /> 术语速查
          </TabsTrigger>
          <TabsTrigger value="recommend" className="gap-1.5">
            <Sparkles className="h-4 w-4" /> 推荐配置
          </TabsTrigger>
        </TabsList>

        <TabsContent value="guide">
          <Card><HowItWorksCard cmdPrefix={cmdPrefix} /></Card>
        </TabsContent>
        <TabsContent value="glossary">
          <Card><ModalityGlossaryCard /></Card>
        </TabsContent>
        <TabsContent value="recommend">
          <Card><RecommendedSetupCard cmdPrefix={cmdPrefix} /></Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ───────────────────────────────────────────────────────────
// 1) AI 命令工作原理（先看这个再去配模型提供商）
// ───────────────────────────────────────────────────────────
function HowItWorksCard({ cmdPrefix }: { cmdPrefix: string }) {
  return (
    <>
      <CardHeader>
        <CardDescription>
          在 TG 任意对话中回复某条消息，发"命令前缀ai"，如：
          <code className="mx-1">{cmdPrefix}ai 你的问题</code>，worker 会用 LLM
          的回答<strong>编辑你刚刚发出去的命令消息</strong>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <ol className="list-decimal space-y-1.5 pl-5 text-muted-foreground">
          <li>
            前缀默认是你系统设置里的命令前缀 <code>{cmdPrefix}</code>（不是 <code>/</code>）；要改去
            <span className="mx-1 font-medium">系统设置 → 命令前缀</span> 改
          </li>
          <li>
            worker 只拦截「自己发给自己 / 别人」的消息（outgoing），别人发的同样命令不会触发
          </li>
          <li>
            被回复消息的正文 + 你跟在命令后的问题被拼成 user prompt，
            其中 <code>system_prompt</code> 由模板配置决定
          </li>
          <li>
            返回结果时<strong>编辑你的命令消息</strong>而不是发新消息；末尾会附上 <code>—
              模型名 · in/out tokens</code>，自动路由模式还会标 <code>auto · 决策原因</code>
          </li>
          <li>
            两步配置才能用：先到 <strong>AI 模块 → 模型提供商</strong> 新建并配置好（填 API Key 等），
            再去 <span className="font-medium">插件 → 命令模板</span> 新建 例如 type=ai
            的模板（命名为 <code>ai</code>），最后在账号详情勾选启用
          </li>
        </ol>
        <div className="rounded-md border px-3 py-2 text-xs alert-warning">
          安全说明：所有 API Key 经主密钥 Fernet 加密落库；GET 接口永远不返明文，
          调用错误的异常消息也会自动剥离 sk- / Bearer 等敏感串。
        </div>
      </CardContent>
    </>
  );
}

// ───────────────────────────────────────────────────────────
// 2) 术语速查：模态 / 标签 / 成本档（解释路由是怎么挑模型的）
// ───────────────────────────────────────────────────────────
function ModalityGlossaryCard() {
  return (
    <>
      <CardHeader>
        <CardDescription>
          配模型提供商时下面三类元数据决定「自动路由」如何挑模型。点击模型提供商编辑里的字段
          会显示同样的解释。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div>
          <h4 className="mb-1.5 font-semibold">模态（modality）— 模型能"看/听/说"什么</h4>
          <ul className="space-y-1 text-xs text-muted-foreground">
            <li>
              <Badge variant="outline" className="mr-1.5">text</Badge>
              纯文本 LLM（绝大多数）。仅支持文本输入文本输出
            </li>
            <li>
              <Badge variant="outline" className="mr-1.5">vision</Badge>
              视觉多模态（VLM, Vision-Language Model）。支持图文输入 → 文本输出，
              典型如 GPT-4V / Claude 3.x Vision / GLM-4V
            </li>
            <li>
              <Badge variant="outline" className="mr-1.5">audio</Badge>
              音频多模态。支持语音转写 (STT) / 文转语音 (TTS) / 实时语音对话，
              典型如 Whisper / GPT-4o realtime audio
            </li>
            <li>
              <Badge variant="outline" className="mr-1.5">multimodal</Badge>
              全模态（Omnimodal）。同时支持图、音、视频等多种输入，
              典型如 GPT-4o / Gemini 2.0 Pro
            </li>
          </ul>
        </div>

        <div>
          <h4 className="mb-1.5 font-semibold">路由标签（tags）— 模型擅长干什么</h4>
          <div className="flex flex-wrap gap-2 text-xs">
            <TagDef tag="chat" desc="通用闲聊 / 短问短答" />
            <TagDef tag="code" desc="代码生成 / 解释 / 调试" />
            <TagDef tag="math" desc="数学推导 / 计算" />
            <TagDef tag="translate" desc="多语种翻译" />
            <TagDef tag="vision" desc="看图说话 / OCR / 图像理解（需配 modality=vision）" />
            <TagDef tag="long_context" desc="大上下文（≥ 64K token）" />
            <TagDef tag="reason" desc="复杂推理 / 多步分析（旗舰）" />
            <TagDef tag="smart" desc="答主力（同 reason，强调质量）" />
            <TagDef tag="cheap" desc="量大优先（成本档 1）" />
            <TagDef tag="fast" desc="低延迟优先" />
            <TagDef tag="classify" desc="路由分类器；轻量小模型" />
          </div>
        </div>

        <div>
          <h4 className="mb-1.5 font-semibold">推理成本档（cost_tier）— 同 tag 多个候选时挑谁</h4>
          <ul className="space-y-1 text-xs text-muted-foreground">
            <li>
              <Badge variant="secondary" className="mr-1.5">tier 1</Badge>
              便宜量产档：路由器在 chat / classify / translate 等高频场景下优先挑这档
            </li>
            <li>
              <Badge variant="secondary" className="mr-1.5">tier 2</Badge>
              中档：默认值；适合绝大多数场景
            </li>
            <li>
              <Badge variant="secondary" className="mr-1.5">tier 3</Badge>
              旗舰档：复杂推理 / smart / reason 等场景路由器优先挑这档
            </li>
          </ul>
        </div>

        <div>
          <h4 className="mb-1.5 font-semibold">路由策略（命中顺序）</h4>
          <ol className="list-decimal space-y-0.5 pl-5 text-xs text-muted-foreground">
            <li>被回复消息含图 / 关键词 → 选 modality∈{"{vision,multimodal}"}</li>
            <li>消息含代码块或 def/function/class 等 token → tag=code</li>
            <li>消息含 LaTeX 或多次"数字+运算符" → tag=math</li>
            <li>消息含「翻译为/translate to」等 → tag=translate</li>
            <li>原文+问题合计 ≥ 1500 字符 → tag=long_context</li>
            <li>消息含「为什么/分析/推导/对比」等 → tag∈{"{reason,smart}"}（旗舰优先）</li>
            <li>都不命中 → tag=chat 中 cost_tier 最低（最便宜）</li>
            <li>全失败 → 调分类器模型提供商让小模型判类（可选）</li>
            <li>仍无 → 用模板里配的「独立兜底模型提供商」</li>
            <li>仍无 → 候选池里 cost_tier 最低的那条</li>
          </ol>
        </div>
      </CardContent>
    </>
  );
}

function TagDef({ tag, desc }: { tag: string; desc: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border bg-background px-2 py-1">
      <Badge variant="outline" className="font-mono">
        {tag}
      </Badge>
      <span className="text-muted-foreground">{desc}</span>
    </span>
  );
}

// ───────────────────────────────────────────────────────────
// 3) 模型推荐配置（针对常见 4 家做的预设）
// ───────────────────────────────────────────────────────────
function RecommendedSetupCard({ cmdPrefix }: { cmdPrefix: string }) {
  // 注意：模型版本号会随官方更新变化；这里只是建议起点，请以各厂商当前可用为准
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
        role: "答主力（旗舰文本 + 视觉）",
        note: "代码、长文、复杂推理优先；做最终回答主力。",
      },
      {
        name: "GPT 5.5",
        protocol: "openai",
        modality: "multimodal",
        tags: ["smart", "reason", "vision"],
        tier: 3,
        role: "通用兜底 + 多模态备份",
        note: "全模态（图/音/视频）兜底；当 Claude Opus 不可用时顶上。",
      },
      {
        name: "GLM 4.7",
        protocol: "openai 兼容（自填 base_url）",
        modality: "text",
        tags: ["chat", "code", "classify", "cheap"],
        tier: 1,
        role: "中文闲聊 + 路由分类器",
        note: "中文短问短答性价比高；最适合做 classifier 让它判路由类别。",
      },
      {
        name: "Mimo V2.5 Pro",
        protocol: "openai 兼容（自填 base_url）",
        modality: "text",
        tags: ["chat", "translate", "cheap", "fast"],
        tier: 1,
        role: "翻译 + 短文闲聊量产",
        note: "中英互译 + 低延迟闲聊场景的量产档；不要给它复杂推理。",
      },
    ];

  return (
    <>
      <CardHeader>
        <CardDescription>
          给四个模型建议的 模态 / 标签 / 推理成本档 组合；按下方填到 AI 模块的模型提供商里即可。
          也可以全部建好后在自定义命令里把一条 <code>{cmdPrefix}ai</code> 设成 auto 模式 +
          GLM 做 classifier，自动路由到合适模型
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>模型</TableHead>
              <TableHead>提供商协议</TableHead>
              <TableHead>模态</TableHead>
              <TableHead>标签</TableHead>
              <TableHead>推理成本档</TableHead>
              <TableHead>定位</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.name}>
                <TableCell className="font-medium">{r.name}</TableCell>
                <TableCell className="font-mono text-xs">{r.protocol}</TableCell>
                <TableCell>
                  <Badge variant="outline">{r.modality}</Badge>
                </TableCell>
                <TableCell className="space-x-1">
                  {r.tags.map((t) => (
                    <Badge key={t} variant="outline" className="text-xs">
                      {t}
                    </Badge>
                  ))}
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">{r.tier}</Badge>
                </TableCell>
                <TableCell className="text-xs">
                  <div className="font-medium">{r.role}</div>
                  <div className="text-muted-foreground">{r.note}</div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        <div className="mt-4 rounded-md border px-3 py-2 text-xs alert-info">
          <p className="font-semibold">推荐落地组合（最省 token + 答主力都顾上）：</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            <li>
              建一条 <code>{cmdPrefix}ai</code> 模板设 auto 模式：
              默认/兜底 = Claude Opus 4.7，分类器 = GLM 4.7
            </li>
            <li>
              再建几条 fixed 模板做强制覆盖：<code>{cmdPrefix}opus</code> / <code>{cmdPrefix}gpt</code> /
              <code>{cmdPrefix}glm</code> / <code>{cmdPrefix}mimo</code> 各绑死一个模型提供商，方便手动选
            </li>
            <li>
              视觉场景在被回复消息含图时会自动用 modality=vision/multimodal 的模型提供商，
              不用单独建 <code>{cmdPrefix}看图</code> 命令
            </li>
          </ul>
        </div>
      </CardContent>
    </>
  );
}
