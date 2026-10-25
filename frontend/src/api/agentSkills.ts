import type { components } from "@/api/schema";
import { api } from "@/lib/api";

export { pluginSkillOptions, sourceLabel } from "@/lib/agentSkillOptions";

export type AgentSkill = components["schemas"]["AgentSkillOut"];
export type AgentSkillDetail = components["schemas"]["AgentSkillDetailOut"];
export type AgentSkillList = components["schemas"]["AgentSkillListOut"];
export type AgentSkillInstall = components["schemas"]["AgentSkillInstallIn"];
export type AgentSkillUpstream = components["schemas"]["AgentSkillUpstreamOut"];
export type AgentSkillUninstall = components["schemas"]["AgentSkillUninstallOut"];

const root = "/api/agent-skills";
const path = (name: string) => `${root}/${encodeURIComponent(name)}`;

export async function listAgentSkills(): Promise<AgentSkillList> {
  const { data } = await api.get<AgentSkillList>(root);
  return data;
}

export async function getAgentSkill(name: string): Promise<AgentSkillDetail> {
  const { data } = await api.get<AgentSkillDetail>(path(name));
  return data;
}

export async function installAgentSkill(payload: AgentSkillInstall): Promise<AgentSkill> {
  // 安装会从源地址拉取，可能较慢；写操作不自动重试。
  const { data } = await api.post<AgentSkill>(root, payload, { timeout: 180_000 });
  return data;
}

export async function patchAgentSkill(
  name: string,
  payload: { enabled_agent?: boolean; enabled_plugins?: boolean },
): Promise<AgentSkill> {
  const { data } = await api.patch<AgentSkill>(path(name), payload);
  return data;
}

export async function updateAgentSkill(
  name: string,
  payload: { skill_md?: string; zip_base64?: string } = {},
): Promise<{ skill: AgentSkill; changed: boolean }> {
  const { data } = await api.post<{ skill: AgentSkill; changed: boolean }>(`${path(name)}/update`, payload, {
    timeout: 180_000,
  });
  return data;
}

export async function editAgentSkill(name: string, skillMd: string): Promise<AgentSkill> {
  const { data } = await api.put<AgentSkill>(`${path(name)}/content`, { skill_md: skillMd });
  return data;
}

export async function getAgentSkillUpstream(name: string): Promise<AgentSkillUpstream> {
  const { data } = await api.get<AgentSkillUpstream>(`${path(name)}/upstream`);
  return data;
}

export async function restoreAgentSkillDefault(name: string): Promise<AgentSkill> {
  const { data } = await api.post<AgentSkill>(`${path(name)}/restore-default`);
  return data;
}

export async function keepAgentSkillModified(name: string): Promise<AgentSkill> {
  const { data } = await api.post<AgentSkill>(`${path(name)}/keep-modified`);
  return data;
}

export async function uninstallAgentSkill(name: string): Promise<AgentSkillUninstall> {
  const { data } = await api.delete<AgentSkillUninstall>(path(name));
  return data;
}

export async function fileToBase64(file: File): Promise<string> {
  const buffer = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunk = 0x8000;
  for (let index = 0; index < buffer.length; index += chunk) {
    binary += String.fromCharCode(...buffer.subarray(index, index + chunk));
  }
  return btoa(binary);
}
