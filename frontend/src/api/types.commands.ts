// 自定义命令域类型：从 OpenAPI 生成类型 `schema.ts` 引出（WP-B12 迁移启动）。
//
// 迁移口径同 `types.accounts.ts`：新代码从这里或 `schema.ts` 引类型，
// `types.ts` 继续 re-export 兼容层。
import type { components as ApiComponents } from "@/api/schema";

type Schemas = ApiComponents["schemas"];

// 4 种命令类型：
//   - reply_text   收到 → 编辑原消息为文本（支持 {args} 占位）
//   - forward_to   收到 → 转发被引用消息到指定 chat_id
//   - run_plugin   占位：调插件方法（V1 暂未实装）
//   - ai           收到 → 调 LLM provider → 编辑回原消息
export type CommandTemplateType =
  | "reply_text"
  | "forward_to"
  | "run_plugin"
  | "ai";

export interface CommandTemplateOut {
  id: number;
  /** ,name 触发名；命令前缀在系统设置里改 */
  name: string;
  type: CommandTemplateType;
  /** 按 type 不同结构；前端按 type 切表单 */
  config: Record<string, unknown>;
  description: string | null;
  aliases: string[];
  created_at: string;
}

/** 新建模板入参（POST /api/commands/templates） */
export interface CommandTemplateCreate {
  name: string;
  aliases?: string[];
  type: CommandTemplateType;
  config: Record<string, unknown>;
  description?: string | null;
}

/** PATCH 更新（任意字段可选） */
export interface CommandTemplateUpdate {
  name?: string;
  aliases?: string[];
  type?: CommandTemplateType;
  config?: Record<string, unknown>;
  description?: string | null;
}

/** 生成侧类型保留为别名，便于前端逐步切换到生成 schema。 */
export type CommandTemplateCreateOut = Schemas["CommandTemplateCreate"];
export type CommandTemplateUpdateOut = Schemas["CommandTemplateUpdate"];

export interface AccountCommandItem {
  template: CommandTemplateOut;
  enabled: boolean;
}

/** AI 总览：账号启用 AI 命令的批量统计 */
export interface AICommandEnablementSummary {
  total_accounts: number;
  enabled_accounts: number;
  ai_templates: number;
}

export interface BuiltinCommandItem {
  name: string;
  aliases: string[];
  doc: string;
}
