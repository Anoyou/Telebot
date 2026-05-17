import React from "react";
import { useQuery } from "@tanstack/react-query";

import { TelegramHtmlPreview } from "@/components/TelegramHtmlPreview";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { getSystemSettings } from "@/api/system";

export type OutputFormat = "html" | "markdown" | "plain";

export interface OutputTemplatePreset {
  key: string;
  label: string;
  tpl: string;
  desc: string;
}

export interface OutputTemplatePlaceholder {
  insert: string;
  label: string;
  desc: string;
}

export interface OutputTemplateConditionalBlock {
  snippet: string;
  label: string;
  desc: string;
}

// 消息格式预设（与后端 services/llm_format.py 的 PRESETS 同源）
//
// 这些字符串必须**逐字**与后端 PRESET_SIMPLE / PRESET_QUOTE / PRESET_MINIMAL /
// PRESET_TRANSLATE 一致。改后端要同步改这里；改这里要同步改后端。
//
// 注意：output_format 默认 'html'（Telethon 1.36 不接受 'markdownv2' 字符串）；
// 这些预设里的 <b> <blockquote expandable> 等是字面 HTML，渲染时只对占位符值做
// HTML 转义，模板自身的标签保留。
export const PRESET_SIMPLE_TEMPLATE =
  "{answer}\n\n— {model} · in {in_tokens} / out {out_tokens}{?routing_note}  ·  {routing_note}{/?}";

export const PRESET_QUOTE_TEMPLATE =
  // 双 blockquote：一段是被回复消息（quoted，媒体类显示 emoji 占位），
  // 一段是用户的问题（question）。任一为空就跳过对应段。
  "{?quoted}<blockquote expandable>{quoted}</blockquote>\n{/?}" +
  "{?question}<blockquote expandable>{question}</blockquote>\n{/?}" +
  "<b>✨ AI 回答</b>\n" +
  "{answer_first_2}" +
  "{?answer_rest}\n<blockquote expandable>{answer_rest}</blockquote>{/?}\n\n" +
  "━━━━━━━━━━━━━━━\n" +
  "{model} · {provider}\n" +
  "In: {in_tokens} | Out: {out_tokens} | Total: {total_tokens}" +
  "{?routing_note}\n{routing_note}{/?}";

export const PRESET_MINIMAL_TEMPLATE = "{answer}\n<code>{model}</code> · {total_tokens}t";

// 翻译/简答风：不显示 quoted（即使 quote_replied=True 仅供模型上下文）
// 适合 ,翻译 / ,简答 / ,润色 等命令
export const PRESET_TRANSLATE_TEMPLATE = "{answer}\n\n<i>— {model}</i>";

export const OUTPUT_TEMPLATE_PRESETS: OutputTemplatePreset[] = [
  { key: "simple", label: "简洁（默认）", tpl: PRESET_SIMPLE_TEMPLATE, desc: "答案 + 一行 footer；任何模式下都好看" },
  { key: "quote", label: "引用风", tpl: PRESET_QUOTE_TEMPLATE, desc: "alma 风；前 2 行 + 折叠剩余（HTML 模式）" },
  { key: "minimal", label: "极简", tpl: PRESET_MINIMAL_TEMPLATE, desc: "答案 + 模型 + 总 tokens" },
  { key: "translate", label: "翻译/简答风", tpl: PRESET_TRANSLATE_TEMPLATE, desc: "不显示被引用原文；适合 ,翻译 / ,简答 这类" },
];

// 占位符按钮元数据；与后端 PLACEHOLDER_META 同源
export const OUTPUT_TEMPLATE_PLACEHOLDERS: OutputTemplatePlaceholder[] = [
  { insert: "{answer}", label: "[回答]", desc: "AI 的回答正文" },
  { insert: "{answer_first_2}", label: "[回答-前2行]", desc: "回答的前 2 行（折叠用）" },
  { insert: "{answer_rest}", label: "[回答-剩余]", desc: "回答从第 3 行起（配 <blockquote expandable> 折叠）" },
  { insert: "{display_input}", label: "[输入]", desc: "用户的输入：被回复消息正文（优先）/ 没有则用问题" },
  { insert: "{display_input_first_2}", label: "[输入-前2行]", desc: "输入的前 2 行（折叠用）" },
  { insert: "{display_input_rest}", label: "[输入-剩余]", desc: "输入从第 3 行起（配 <blockquote expandable> 折叠）" },
  { insert: "{question}", label: "[问题]", desc: "用户在命令后跟的问题" },
  { insert: "{quoted}", label: "[被引用]", desc: "被回复消息的正文（无被回复时为空）" },
  { insert: "{model}", label: "[模型]", desc: "API 实际返回的模型名" },
  { insert: "{provider}", label: "[提供商]", desc: "提供商名称（如 Any GPT）" },
  { insert: "{provider_kind}", label: "[厂商]", desc: "openai / anthropic / ollama" },
  { insert: "{in_tokens}", label: "[输入tokens]", desc: "输入 token 数" },
  { insert: "{out_tokens}", label: "[输出tokens]", desc: "输出 token 数" },
  { insert: "{total_tokens}", label: "[总tokens]", desc: "输入 + 输出" },
  { insert: "{routing_note}", label: "[路由说明]", desc: "auto 模式的决策原因（fixed 模式空）" },
  { insert: "{sources}", label: "[来源]", desc: "联网搜索返回的来源列表（无来源时为空）" },
  { insert: "{time}", label: "[时间]", desc: "当前时间 HH:MM" },
];

export const OUTPUT_TEMPLATE_CONDITIONAL_BLOCKS: OutputTemplateConditionalBlock[] = [
  {
    snippet: "{?quoted}\n\n{/?}",
    label: "[条件:被引用]",
    desc: "仅当被回复消息非空才渲染括号内",
  },
  {
    snippet: "{?routing_note}\n\n{/?}",
    label: "[条件:路由]",
    desc: "仅 auto 模式才渲染括号内",
  },
  {
    snippet: "{?sources}\n\n<b>来源</b>\n{sources}{/?}",
    label: "[条件:来源]",
    desc: "仅联网搜索返回来源时渲染",
  },
  {
    snippet: "{?answer_rest}\n<blockquote expandable>{answer_rest}</blockquote>{/?}",
    label: "[条件:回答有剩余]",
    desc: "仅当回答超过 2 行才渲染（配折叠块用）",
  },
  {
    snippet: "{?display_input_rest}\n<blockquote expandable>{display_input_rest}</blockquote>{/?}",
    label: "[条件:输入有剩余]",
    desc: "仅当输入超过 2 行才渲染（配折叠块用）",
  },
];

export function renderOutputTemplatePreview(template: string, values: Record<string, string>): string {
  let out = template || PRESET_SIMPLE_TEMPLATE;
  out = out.replace(/\{\?([a-zA-Z0-9_]+)\}([\s\S]*?)\{\/\?\}/g, (_, key: string, inner: string) =>
    values[key] ? inner : "",
  );
  out = out.replace(/\{([a-zA-Z0-9_]+)\}/g, (_, key: string) => values[key] ?? "");
  return out;
}

export interface OutputTemplateEditorProps {
  outputFormat: OutputFormat;
  onOutputFormatChange: (v: OutputFormat) => void;
  template: string;
  onTemplateChange: (v: string) => void;
  escapeValues: boolean;
  onEscapeValuesChange: (v: boolean) => void;
}

export function OutputTemplateEditor({
  outputFormat,
  onOutputFormatChange,
  template,
  onTemplateChange,
  escapeValues,
  onEscapeValuesChange,
}: OutputTemplateEditorProps) {
  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const cmdPrefix = settingsQ.data?.command_prefix || ",";
  const textareaRef = React.useRef<HTMLTextAreaElement | null>(null);
  const previewText = renderOutputTemplatePreview(template, {
    answer: "这是 AI 回答示例，已按当前消息模板渲染。",
    answer_first_2: "这是 AI 回答示例，已按当前消息模板渲染。",
    answer_rest: "这里是从第三行开始的回答内容。",
    display_input: "被回复消息或用户问题示例",
    display_input_first_2: "被回复消息或用户问题示例",
    display_input_rest: "这里是输入内容的剩余部分。",
    question: "请总结这段内容",
    quoted: "这是一段被回复的原文。",
    model: "gpt-5.4",
    provider: "OpenAI",
    provider_kind: "openai",
    in_tokens: "128",
    out_tokens: "64",
    total_tokens: "192",
    routing_note: "auto: chat",
    sources: "1. OpenAI 文档\nhttps://platform.openai.com/docs\n2. 示例来源\nhttps://example.com",
    time: "12:30",
  });

  // 在光标位置插入文本，光标停在插入末尾
  const insertAtCursor = (text: string) => {
    const ta = textareaRef.current;
    if (!ta) {
      onTemplateChange((template || "") + text);
      return;
    }
    const start = ta.selectionStart ?? template.length;
    const end = ta.selectionEnd ?? template.length;
    const next = template.slice(0, start) + text + template.slice(end);
    onTemplateChange(next);
    // 在 React 下次 render 后把光标停到插入末尾
    queueMicrotask(() => {
      ta.focus();
      const pos = start + text.length;
      ta.setSelectionRange(pos, pos);
    });
  };

  // "应用预设"按钮处理：直接覆盖 textarea
  const applyPreset = (tpl: string) => {
    onTemplateChange(tpl);
    queueMicrotask(() => textareaRef.current?.focus());
  };

  return (
    <div className="rounded-md border bg-muted/30 p-3 space-y-3">
      <div>
        <Label className="text-sm font-semibold">消息格式</Label>
        <p className="text-xs text-muted-foreground">
          决定 {cmdPrefix}ai 调用后编辑回 TG 的消息长什么样。留空 = 用"简洁"预设。
          支持的占位符见下方按钮，点击直接插入光标位置。
        </p>
      </div>

      {/* 解析模式 */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label className="text-xs">解析模式（parse_mode）</Label>
          <Select
            value={outputFormat}
            onChange={(e) => onOutputFormatChange(e.target.value as OutputFormat)}
          >
            <option value="html">HTML（推荐；支持 &lt;b&gt; &lt;blockquote expandable&gt; 折叠引用）</option>
            <option value="markdown">Markdown v1（**bold** / `code` / [link](url)；不支持折叠）</option>
            <option value="plain">纯文本（不解析任何格式）</option>
          </Select>
          <p className="text-[11px] text-muted-foreground">
            注：Telethon 1.36 不识别 MarkdownV2；要折叠引用块请用 HTML 模式 +
            <code>&lt;blockquote expandable&gt;</code>
          </p>
        </div>
        <div className="flex items-center gap-2 self-end pb-2">
          <Switch
            checked={escapeValues}
            onCheckedChange={onEscapeValuesChange}
            id="escapeValues"
          />
          <Label htmlFor="escapeValues" className="cursor-pointer text-xs">
            自动转义占位符值
          </Label>
        </div>
      </div>
      {!escapeValues && (
        <p className="rounded-md border px-3 py-1.5 text-xs alert-warning">
          ⚠ 关闭自动转义后，{"{answer}"} 里的 markdown 字符会被 TG 解析为格式（高级用法）；
          解析失败时本条命令会回落为纯文本展示
        </p>
      )}

      {/* 预设 */}
      <div className="space-y-1.5">
        <Label className="text-xs">快捷预设（直接覆盖下方模板）</Label>
        <div className="flex flex-wrap gap-1.5">
          {OUTPUT_TEMPLATE_PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => applyPreset(p.tpl)}
              title={p.desc}
              className="rounded-full border px-2.5 py-0.5 text-xs hover:bg-muted"
            >
              {p.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => onTemplateChange("")}
            title="清空：保存后将自动用'简洁'预设"
            className="rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground hover:bg-muted"
          >
            清空（用默认）
          </button>
        </div>
      </div>

      {/* 占位符按钮 */}
      <div className="space-y-1.5">
        <Label className="text-xs">占位符（点击插入光标位置）</Label>
        <div className="flex flex-wrap gap-1">
          {OUTPUT_TEMPLATE_PLACEHOLDERS.map((b) => (
            <button
              key={b.insert}
              type="button"
              onClick={() => insertAtCursor(b.insert)}
              title={b.desc}
              className="rounded border px-1.5 py-0.5 text-[11px] font-mono hover:bg-muted"
            >
              {b.label}
            </button>
          ))}
        </div>
        <Label className="text-xs">条件块（仅在条件为真时渲染括号内）</Label>
        <div className="flex flex-wrap gap-1">
          {OUTPUT_TEMPLATE_CONDITIONAL_BLOCKS.map((b) => (
            <button
              key={b.label}
              type="button"
              onClick={() => insertAtCursor(b.snippet)}
              title={b.desc}
              className="rounded border px-1.5 py-0.5 text-[11px] font-mono hover:bg-muted"
            >
              {b.label}
            </button>
          ))}
        </div>
      </div>

      {/* 模板 textarea */}
      <div className="space-y-1.5">
        <Label className="text-xs">模板（≤ 4000 字符）</Label>
        <Textarea
          ref={textareaRef}
          value={template}
          rows={10}
          maxLength={4000}
          onChange={(e) => onTemplateChange(e.target.value)}
          placeholder={"留空 = 用'简洁'预设。\n试试上面的预设按钮先填一个再改。"}
          className="font-mono text-xs"
        />
        <p className="text-xs text-muted-foreground">
          剩余 {4000 - (template || "").length} 字符。{template.length === 0 ? "（已留空，会用默认）" : ""}
        </p>
      </div>

      <div className="rounded-md border bg-background p-3 text-xs">
        <div className="mb-1 font-medium">预览</div>
        <TelegramHtmlPreview value={previewText} mode={outputFormat} />
      </div>
    </div>
  );
}
