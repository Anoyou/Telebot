import assert from "node:assert/strict";
import test from "node:test";

import type { components } from "@/api/schema";
import { pluginSkillOptions, sourceLabel } from "./agentSkillOptions.ts";

type AgentSkill = components["schemas"]["AgentSkillOut"];

function skill(partial: Partial<AgentSkill>): AgentSkill {
  return {
    name: "x",
    description: "d",
    builtin: false,
    source_type: "paste",
    enabled_agent: true,
    enabled_plugins: false,
    plugin_supported: true,
    updatable: false,
    ...partial,
  } as AgentSkill;
}

test("only offers plugin-enabled, plugin-capable skills to plugin config", () => {
  const options = pluginSkillOptions([
    skill({ name: "a", enabled_plugins: true }),
    skill({ name: "b", enabled_plugins: false }),
    skill({ name: "accounts", builtin: true, plugin_supported: false, source_type: "builtin" }),
    skill({ name: "group-helper", enabled_plugins: true, source_type: "plugin_bundled", owner_plugin: "interaction-ai-chat" }),
  ]);
  assert.deepEqual(options.map((o) => o.value), ["a", "group-helper"]);
});

test("labels skill sources in Chinese", () => {
  assert.equal(sourceLabel({ source_type: "plugin_bundled", owner_plugin: "interaction-ai-chat" }), "插件内置 · interaction-ai-chat");
  assert.equal(sourceLabel({ source_type: "md_upload", owner_plugin: null }), "上传 SKILL.md");
  assert.equal(sourceLabel({ source_type: "builtin", owner_plugin: null }), "TelePilot 内置");
});
