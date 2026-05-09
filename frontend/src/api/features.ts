// 功能矩阵 / 规则 / 自动回复 dry-run 等 API 包装
import { api } from "@/lib/api";
import type {
  FeatureMatrixResponse,
  RuleCopyRequest,
  RuleCreate,
  RuleDryRunRequest,
  RuleDryRunResponse,
  RuleExecuteResponse,
  RuleOut,
  RuleUpdate,
} from "@/api/types";

export async function getFeatureMatrix(): Promise<FeatureMatrixResponse> {
  const { data } = await api.get<FeatureMatrixResponse>("/api/feature-matrix");
  return data;
}

// ===================== 插件配置 API =====================

/** 获取插件的 global config */
export async function getPluginGlobalConfig(pluginKey: string): Promise<Record<string, unknown>> {
  const { data } = await api.get<{ plugin_key: string; config: Record<string, unknown> }>(
    `/api/plugins/${pluginKey}/config`
  );
  return data.config;
}

/** 设置插件的 global config */
export async function setPluginGlobalConfig(
  pluginKey: string,
  config: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const { data } = await api.put<{ plugin_key: string; config: Record<string, unknown> }>(
    `/api/plugins/${pluginKey}/config`,
    { config }
  );
  return data.config;
}

/** 获取某账号某插件的最终生效配置（合并后） */
export async function getEffectiveConfig(
  aid: number,
  pluginKey: string
): Promise<Record<string, unknown>> {
  const { data } = await api.get<Record<string, unknown>>(
    `/api/accounts/${aid}/features/${pluginKey}/config`
  );
  return data;
}

/** 更新账号级插件配置（仅更新 config，不改变 enabled） */
export async function updateAccountFeatureConfig(
  aid: number,
  pluginKey: string,
  config: Record<string, unknown>
): Promise<void> {
  await api.patch(`/api/accounts/${aid}/features/${pluginKey}/config`, { config });
}

/** 验证配置是否符合 schema */
export async function validatePluginConfig(
  pluginKey: string,
  config: Record<string, unknown>
): Promise<{ valid: boolean; errors: Array<{ field: string; message: string }> }> {
  const { data } = await api.post<{ valid: boolean; errors: Array<{ field: string; message: string }> }>(
    `/api/plugins/${pluginKey}/config/validate`,
    { config }
  );
  return data;
}

// ===================== 规则 API =====================

export async function listRules(
  aid: number,
  feature: string,
): Promise<RuleOut[]> {
  const { data } = await api.get<RuleOut[]>(
    `/api/accounts/${aid}/features/${feature}/rules`,
  );
  return data;
}

export async function createRule(
  aid: number,
  feature: string,
  payload: RuleCreate,
): Promise<RuleOut> {
  const { data } = await api.post<RuleOut>(
    `/api/accounts/${aid}/features/${feature}/rules`,
    payload,
  );
  return data;
}

export async function updateRule(
  aid: number,
  feature: string,
  rid: number,
  payload: RuleUpdate,
): Promise<RuleOut> {
  const { data } = await api.patch<RuleOut>(
    `/api/accounts/${aid}/features/${feature}/rules/${rid}`,
    payload,
  );
  return data;
}

export async function deleteRule(
  aid: number,
  feature: string,
  rid: number,
): Promise<void> {
  await api.delete(`/api/accounts/${aid}/features/${feature}/rules/${rid}`);
}

export async function dryRunRule(
  aid: number,
  feature: string,
  rid: number,
  payload: RuleDryRunRequest,
): Promise<RuleDryRunResponse> {
  const { data } = await api.post<RuleDryRunResponse>(
    `/api/accounts/${aid}/features/${feature}/rules/${rid}/dry-run`,
    payload,
  );
  return data;
}

export async function executeRule(
  aid: number,
  feature: string,
  rid: number,
): Promise<RuleExecuteResponse> {
  const { data } = await api.post<RuleExecuteResponse>(
    `/api/accounts/${aid}/features/${feature}/rules/${rid}/execute`,
  );
  return data;
}

export async function copyRules(
  aid: number,
  feature: string,
  payload: RuleCopyRequest,
): Promise<void> {
  await api.post(
    `/api/accounts/${aid}/features/${feature}/rules/copy`,
    payload,
  );
}
