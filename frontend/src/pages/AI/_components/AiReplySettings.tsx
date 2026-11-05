import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getSystemSettings, patchSystemSettings } from "@/api/system";
import { getErrMsg } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/misc";

/**
 * 「AI 回复排版与发送」（0.120.1）：AI 回复相关的服务器参数集中放在 AI 页。
 * 以前藏在「服务器设置 › 平台与模块 › 指令前缀」卡片里；后端键名不变。
 */

export type AiReplyStyle = "normal" | "edit_rich" | "inline";
export type AiReplySwitchKey =
  | "ai_plugin_reply_via_bot"
  | "ai_stream_edit"
  | "ai_layout_divider_before"
  | "ai_layout_divider_after"
  | "ai_native_divider"
  | "ai_inline_ask_enabled"
  | "ai_inline_ask_show_question";

export const AI_REPLY_SWITCH_DEFAULTS: Record<AiReplySwitchKey, boolean> = {
  ai_plugin_reply_via_bot: true,
  ai_stream_edit: false,
  ai_layout_divider_before: true,
  ai_layout_divider_after: true,
  ai_native_divider: true,
  ai_inline_ask_enabled: true,
  ai_inline_ask_show_question: true,
};

type SwitchItem = { key: AiReplySwitchKey; label: string; hint: string };

const SEND_ITEMS: SwitchItem[] = [
  {
    key: "ai_plugin_reply_via_bot",
    label: "Bot 在群里时由 Bot 回复",
    hint: "AI 答疑插件被关键词或 @Bot 触发时（包括你自己触发），交互 Bot 就在这个群里就由 Bot 直接回复你的消息；Bot 不在群里才借你的账号代发。关掉则在「inline 代发」时照旧用你的账号发“你 via @Bot”。",
  },
  {
    key: "ai_stream_edit",
    label: "边写边显示（分段流式）",
    hint: "回复方式是「普通」或「在原消息上编辑」时，每写好一段就先显示出来（末尾标「生成中」），写完再整理成完整排版。群里约 3 秒、私聊约 2 秒更新一次。默认关。",
  },
];

const LAYOUT_ITEMS: SwitchItem[] = [
  {
    key: "ai_layout_divider_before",
    label: "问题和回答之间加分割线",
    hint: "回答上面有引用的问题或被回复的消息时，在引用下面加一条线再接回答。模板里已经有线就不重复。",
  },
  {
    key: "ai_layout_divider_after",
    label: "回答和署名之间加分割线",
    hint: "回答下面有署名、模型名或来源时，中间加一条线，署名单独一行。",
  },
  {
    key: "ai_native_divider",
    label: "用 Telegram 自带的细分割线",
    hint: "富文本回答里用 Telegram 原生的细横线；关掉则用一整行「━━━」。普通格式的回答始终是整行「━」。",
  },
];

const INLINE_ITEMS: SwitchItem[] = [
  {
    key: "ai_inline_ask_enabled",
    label: "响应「@Bot 问题」提问",
    hint: "交互 Bot 开了 inline 模式后，群友输入「@交互Bot 问题」会弹出「发送这个问题」，点了 AI 就把回答写进这条消息。关掉则不弹出。",
  },
  {
    key: "ai_inline_ask_show_question",
    label: "回答顶部显示问题",
    hint: "inline 提问的回答顶部用引用块显示原来的问题；关掉只显示回答。",
  },
];

export function normalizeAiReplyStyle(value: unknown): AiReplyStyle {
  return value === "inline" || value === "edit_rich" ? value : "normal";
}

export function readAiReplySwitches(data: Partial<Record<AiReplySwitchKey, boolean | undefined>> | undefined): Record<AiReplySwitchKey, boolean> {
  const out = { ...AI_REPLY_SWITCH_DEFAULTS };
  for (const key of Object.keys(out) as AiReplySwitchKey[]) {
    const value = data?.[key];
    if (typeof value === "boolean") out[key] = value;
  }
  return out;
}

function ParamKey({ k }: { k: string }) {
  return (
    <code aria-hidden="true" className="mt-0.5 block truncate text-[11px] font-normal text-muted-foreground" title={k}>
      {k}
    </code>
  );
}

function SwitchRow({
  item,
  checked,
  disabled,
  onChange,
}: {
  item: { key: string; label: string; hint: string };
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0 space-y-1">
        <div className="text-sm font-medium">
          {item.label}
          <ParamKey k={item.key} />
        </div>
        <p className="text-xs leading-5 text-muted-foreground">{item.hint}</p>
      </div>
      <Switch aria-label={item.label} checked={checked} disabled={disabled} onCheckedChange={onChange} />
    </div>
  );
}

export function AiReplySettings() {
  const qc = useQueryClient();
  const settingsQ = useQuery({ queryKey: ["system", "settings"], queryFn: getSystemSettings });
  const [style, setStyle] = useState<AiReplyStyle>("normal");
  const [deleteCommand, setDeleteCommand] = useState(true);
  const [switches, setSwitches] = useState<Record<AiReplySwitchKey, boolean>>(AI_REPLY_SWITCH_DEFAULTS);

  useEffect(() => {
    if (!settingsQ.data) return;
    setStyle(normalizeAiReplyStyle(settingsQ.data.ai_reply_style));
    setDeleteCommand(settingsQ.data.ai_inline_delete_command ?? true);
    setSwitches(readAiReplySwitches(settingsQ.data));
  }, [settingsQ.data]);

  const save = useMutation({
    mutationFn: (
      patch: { ai_reply_style?: AiReplyStyle; ai_inline_delete_command?: boolean } & Partial<Record<AiReplySwitchKey, boolean>>,
    ) => patchSystemSettings(patch),
    onSuccess: (data) => {
      setStyle(normalizeAiReplyStyle(data.ai_reply_style));
      setDeleteCommand(data.ai_inline_delete_command ?? true);
      setSwitches(readAiReplySwitches(data));
      toast.success("已保存，下一条 AI 回答生效");
      qc.invalidateQueries({ queryKey: ["system", "settings"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const busy = save.isPending;
  const renderSwitch = (item: SwitchItem) => (
    <SwitchRow
      key={item.key}
      item={item}
      checked={switches[item.key]}
      disabled={busy}
      onChange={(checked) => save.mutate({ [item.key]: checked })}
    />
  );

  return (
    <Card data-testid="ai-reply-settings">
      <CardHeader>
        <CardTitle className="text-base">AI 回复排版与发送</CardTitle>
        <CardDescription>
          AI 的回答由谁发、怎么排版。排版统一为：问题引用 → 分割线 → 回答 → 分割线 → 署名。改完下一条回答就生效，不用重启。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {settingsQ.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-9 w-full max-w-md" />
            <Skeleton className="h-16 w-full max-w-xl" />
          </div>
        ) : (
          <>
            <section className="max-w-2xl space-y-3 rounded-md border bg-muted/20 px-3 py-3">
              <h3 className="text-sm font-semibold">谁来发</h3>
              <div className="space-y-1.5">
                <div>
                  <Label htmlFor="ai-reply-style">你发 AI 指令时的回复方式</Label>
                  <ParamKey k="ai_reply_style" />
                </div>
                <Select
                  id="ai-reply-style"
                  value={style}
                  disabled={busy}
                  onChange={(e) => {
                    const next = normalizeAiReplyStyle(e.target.value);
                    setStyle(next);
                    save.mutate({ ai_reply_style: next });
                  }}
                >
                  <option value="normal">普通（把你的指令消息改成回答）</option>
                  <option value="edit_rich">在原消息上编辑（富文本，需 Premium）</option>
                  <option value="inline">inline 代发（交互 Bot 富文本）</option>
                </Select>
                <p className="text-xs leading-5 text-muted-foreground">
                  普通 / 在原消息上编辑：都是改你自己发的那条指令；富文本版能显示细分割线和表格，账号不是 Premium 时自动用普通格式。
                  inline 代发：出现一条“你的名字 via @交互Bot”的消息，由交互 Bot 写成富文本答案；需要先在 @BotFather 给交互 Bot 打开
                  /setinline 和 /setinlinefeedback，不行时自动改用普通。
                </p>
              </div>
              <SwitchRow
                item={{
                  key: "ai_inline_delete_command",
                  label: "inline 代发后删除指令消息",
                  hint: "只对 inline 代发有效：开着聊天里只留答案；关掉则保留你的指令，答案回复在它下面。",
                }}
                checked={deleteCommand}
                disabled={busy || style !== "inline"}
                onChange={(checked) => save.mutate({ ai_inline_delete_command: checked })}
              />
              {SEND_ITEMS.map(renderSwitch)}
            </section>
            <section className="max-w-2xl space-y-3 rounded-md border bg-muted/20 px-3 py-3">
              <h3 className="text-sm font-semibold">排版</h3>
              {LAYOUT_ITEMS.map(renderSwitch)}
            </section>
            <section className="max-w-2xl space-y-3 rounded-md border bg-muted/20 px-3 py-3">
              <h3 className="text-sm font-semibold">@Bot inline 提问</h3>
              {INLINE_ITEMS.map(renderSwitch)}
            </section>
          </>
        )}
      </CardContent>
    </Card>
  );
}
