import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, FileText, Save } from "lucide-react";
import { toast } from "sonner";

import { listCommandTemplates, patchCommandTemplate } from "@/api/commands";
import type { AICommandConfig } from "@/api/types";
import { OutputTemplateEditor, type OutputFormat } from "@/components/ai/OutputTemplateEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { AIPageShell, EmptyState, FieldKV } from "@/pages/AI/_shared";

export function AIOutput() {
  const qc = useQueryClient();
  const templatesQ = useQuery({ queryKey: ["cmd-tpl"], queryFn: listCommandTemplates });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("html");
  const [templateText, setTemplateText] = useState("");
  const [escapeValues, setEscapeValues] = useState(true);
  const [savedSig, setSavedSig] = useState("");

  const aiTemplates = useMemo(
    () => (templatesQ.data || []).filter((t) => t.type === "ai"),
    [templatesQ.data],
  );
  const selectedTemplate = aiTemplates.find((t) => t.id === selectedId) || aiTemplates[0] || null;

  useEffect(() => {
    if (!selectedTemplate) return;
    if (selectedId !== selectedTemplate.id) setSelectedId(selectedTemplate.id);
    const cfg = selectedTemplate.config as Partial<AICommandConfig>;
    const nextFormat = cfg.output_format === "markdown" || cfg.output_format === "plain" ? cfg.output_format : "html";
    const nextTemplate = typeof cfg.output_template === "string" ? cfg.output_template : "";
    const nextEscape = cfg.escape_values !== false;
    setOutputFormat(nextFormat);
    setTemplateText(nextTemplate);
    setEscapeValues(nextEscape);
    setSavedSig(JSON.stringify({ nextFormat, nextTemplate, nextEscape }));
  }, [selectedTemplate?.id]);

  const saveMut = useMutation({
    mutationFn: async () => {
      if (!selectedTemplate) return null;
      const config: Record<string, unknown> = { ...selectedTemplate.config };
      config.output_format = outputFormat;
      config.escape_values = escapeValues;
      if (templateText.trim()) {
        config.output_template = templateText;
      } else {
        delete config.output_template;
      }
      return patchCommandTemplate(selectedTemplate.id, { config });
    },
    onSuccess: async () => {
      toast.success("输出模板已保存");
      await qc.invalidateQueries({ queryKey: ["cmd-tpl"] });
      setSavedSig(JSON.stringify({ nextFormat: outputFormat, nextTemplate: templateText, nextEscape: escapeValues }));
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail || err?.message || "保存失败"),
  });

  if (templatesQ.isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  const customOutputTemplates = aiTemplates.filter((t) => typeof t.config?.output_template === "string");
  const isDirty = selectedTemplate
    ? savedSig !== JSON.stringify({ nextFormat: outputFormat, nextTemplate: templateText, nextEscape: escapeValues })
    : false;

  return (
    <AIPageShell
      title="输出模板"
      description="控制 AI 回复在 Telegram 中的排版，可组合 question、quoted、answer、sources 等占位符。"
      actions={
        <Button asChild size="sm">
          <Link to="/plugins/templates">
            完整编辑命令
            <ArrowRight className="ml-1 h-4 w-4" />
          </Link>
        </Button>
      }
    >
      <div className="grid gap-3 md:grid-cols-3">
        <FieldKV label="AI 模板" value={aiTemplates.length} />
        <FieldKV label="自定义输出" value={customOutputTemplates.length} />
        <FieldKV label="默认格式" value="HTML" />
      </div>

      {aiTemplates.length === 0 ? (
        <EmptyState
          title="还没有 AI 命令模板"
          description="先创建一条 type=AI 的命令模板，再回来设置它的 Telegram 输出格式。"
          actionHref="/plugins/templates"
          actionLabel="创建 AI 命令模板"
        />
      ) : (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <CardTitle className="inline-flex items-center gap-2 text-base">
                  <FileText className="h-4 w-4" /> 模板编辑
                </CardTitle>
                <CardDescription>选择一条 AI 命令，直接调整它的输出模板与预览。</CardDescription>
              </div>
              <Button size="sm" onClick={() => saveMut.mutate()} disabled={!selectedTemplate || !isDirty || saveMut.isPending}>
                <Save className="mr-1 h-4 w-4" /> {saveMut.isPending ? "保存中" : "保存"}
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 md:grid-cols-[minmax(220px,360px)_1fr]">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">AI 命令模板</label>
                <Select value={String(selectedTemplate?.id || "")} onChange={(e) => setSelectedId(Number(e.target.value))}>
                  {aiTemplates.map((template) => (
                    <option key={template.id} value={template.id}>
                      ,{template.name}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="flex flex-wrap items-end gap-2 text-xs text-muted-foreground">
                <Badge variant={templateText.trim() ? "success" : "outline"}>{templateText.trim() ? "自定义模板" : "默认模板"}</Badge>
                <span>{selectedTemplate?.description || "未填写说明"}</span>
              </div>
            </div>
            <OutputTemplateEditor
              outputFormat={outputFormat}
              onOutputFormatChange={setOutputFormat}
              template={templateText}
              onTemplateChange={setTemplateText}
              escapeValues={escapeValues}
              onEscapeValuesChange={setEscapeValues}
            />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">模板说明</CardTitle>
          <CardDescription>
            未设置 output_template 时使用后端默认样式；开启自定义后建议保持 escape_values=true。
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2 text-sm text-muted-foreground md:grid-cols-2">
          <div className="rounded-md border p-3">{"{question}：用户命令后的问题文本"}</div>
          <div className="rounded-md border p-3">{"{quoted}：被回复消息的引用内容"}</div>
          <div className="rounded-md border p-3">{"{answer}：模型生成的主要回答"}</div>
          <div className="rounded-md border p-3">{"{sources}：联网搜索返回的来源信息"}</div>
        </CardContent>
      </Card>

      <div className="grid gap-3 lg:grid-cols-2">
        {aiTemplates.map((template) => (
          <Card key={template.id}>
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between gap-3">
                <CardTitle className="text-base">,{template.name}</CardTitle>
                <Badge variant={typeof template.config?.output_template === "string" ? "success" : "outline"}>
                  {typeof template.config?.output_template === "string" ? "自定义" : "默认"}
                </Badge>
              </div>
              <CardDescription>{template.description || "未填写说明"}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-muted-foreground">
              <div>输出格式：{String(template.config?.output_format || "html")}</div>
              <div>转义占位符：{template.config?.escape_values === false ? "关闭" : "开启"}</div>
            </CardContent>
          </Card>
        ))}
      </div>
    </AIPageShell>
  );
}
