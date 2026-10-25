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

