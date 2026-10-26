/** Agent Skills 纯函数（无运行时依赖，便于 node:test 直接测试）。 */

import type { components } from "@/api/schema";

type AgentSkill = components["schemas"]["AgentSkillOut"];

/** 插件配置多选：只列出对插件启用、可提供给插件的 skill（内置领域技能不提供给插件）。 */
export function pluginSkillOptions(items: AgentSkill[] | undefined): { value: string; label: string; description: string }[] {
  return (items ?? [])
    .filter((item) => item.plugin_supported && item.enabled_plugins)
    .map((item) => ({ value: item.name, label: item.name, description: item.description }));
}

export function sourceLabel(item: Pick<AgentSkill, "source_type" | "owner_plugin">): string {
  switch (item.source_type) {
    case "builtin":
      return "TelePilot 内置";
    case "plugin_bundled":
      return `插件内置${item.owner_plugin ? ` · ${item.owner_plugin}` : ""}`;
    case "github":
      return "GitHub";
    case "zip_url":
      return "zip 链接";
    case "zip_upload":
      return "上传 zip";
    case "md_upload":
      return "上传 SKILL.md";
    case "paste":
      return "粘贴";
    default:
      return item.source_type;
  }
}

/** Web 端 Skills 的入口位置（所有提示文案统一引用，入口变动时只改这里）。 */
export const AGENT_SKILLS_WEB_ENTRY = "助手页顶部「Skills」按钮";

/** 直接打开助手页 Skills 对话框的深链。 */
export const AGENT_SKILLS_WEB_PATH = "/assistant?skills=1";

export const AGENT_SKILLS_INTRO =
  "Skill 是一份 SKILL.md 说明（Anthropic Agent Skills 格式）。助手平时只看到名称和用途，需要时才读取正文；其中脚本永不执行。可分别开放给系统 Agent 与插件，插件还需在自己的配置里选择。";
