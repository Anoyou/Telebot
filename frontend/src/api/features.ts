// 功能矩阵 / 规则 / 自动回复 dry-run 等 API 包装
import { api } from "@/lib/api";
import type {
  FeatureMatrixResponse,
  RuleCopyRequest,
  RuleCreate,
  RuleDryRunRequest,
  RuleDryRunResponse,
  RuleOut,
  RuleUpdate,
} from "@/api/types";

export async function getFeatureMatrix(): Promise<FeatureMatrixResponse> {
  const { data } = await api.get<FeatureMatrixResponse>("/api/feature-matrix");
  return data;
}

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
