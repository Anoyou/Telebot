// 风控 / 系统 API 包装
import { api } from "@/lib/api";
import type {
  AccountRateLimitOut,
  AuditLogItem,
  BackendVersionInfo,
  HealthOverview,
  HumanizeConfig,
  HumanizeUpdate,
  RateLimitRuleConfig,
  StrictRequest,
  RuntimeLogItem,
  SystemSettings,
  TemplateOut,
} from "@/api/types";

// ===================== 版本号（0.4.2 加） =====================
// 后端 GET /api/system/version 是 public 端点（无鉴权），用于前后端版本号对比。
// 不一致时 sidebar 顶部弹红条提示用户 make restart + 硬刷浏览器。
export async function getBackendVersion(): Promise<BackendVersionInfo> {
  const { data } = await api.get<BackendVersionInfo>("/api/system/version");
  return data;
}

// ===================== 风控 =====================
export async function getAccountRateLimit(
  aid: number,
): Promise<AccountRateLimitOut> {
  const { data } = await api.get<AccountRateLimitOut>(
    `/api/accounts/${aid}/rate-limit`,
  );
  return data;
}

export async function patchAccountRateLimit(
  aid: number,
  action: string,
  payload: Partial<RateLimitRuleConfig>,
): Promise<void> {
  await api.patch(`/api/accounts/${aid}/rate-limit/${action}`, payload);
}

export async function strictRateLimit(
  aid: number,
  payload: StrictRequest = {},
): Promise<void> {
  await api.post(`/api/accounts/${aid}/rate-limit/strict`, payload);
}

// ===================== 日志 =====================
export interface RuntimeLogQuery {
  account_id?: number | string;
  level?: string;
  /** "system" = worker 启停 / 错误；"event" = 消息事件 / plugin 命中 */
  source?: "system" | "event" | string;
  since?: string;
  limit?: number;
}
export async function listRuntimeLogs(
  q: RuntimeLogQuery = {},
): Promise<RuntimeLogItem[]> {
  const { data } = await api.get<RuntimeLogItem[]>("/api/logs/runtime", {
    params: q,
  });
  return data;
}

// 操作日志（Dashboard 摘要 + 后续审计页用）
export interface AuditLogQuery {
  user_id?: number;
  since?: string;
  limit?: number;
}
export async function listAuditLogs(
  q: AuditLogQuery = {},
): Promise<AuditLogItem[]> {
  const { data } = await api.get<AuditLogItem[]>("/api/logs/audit", {
    params: q,
  });
  return data;
}

// ===================== 系统设置 =====================
export async function getSystemSettings(): Promise<SystemSettings> {
  const { data } = await api.get<SystemSettings>("/api/system/settings");
  return data;
}
export async function patchSystemSettings(
  payload: Partial<SystemSettings>,
): Promise<SystemSettings> {
  const { data } = await api.patch<SystemSettings>(
    "/api/system/settings",
    payload,
  );
  return data;
}

export async function getGlobalLimits(): Promise<{ api_qps_total: number }> {
  const { data } = await api.get<{ api_qps_total: number }>(
    "/api/system/global-limits",
  );
  return data;
}
export async function putGlobalLimits(api_qps_total: number): Promise<void> {
  await api.put("/api/system/global-limits", { api_qps_total });
}

// ===================== 风控模板 =====================
export async function listRateTemplates(): Promise<TemplateOut[]> {
  const { data } = await api.get<TemplateOut[]>("/api/rate-templates");
  return data;
}

export async function createRateTemplate(payload: {
  name: string;
  is_default?: boolean;
}): Promise<TemplateOut> {
  const { data } = await api.post<TemplateOut>("/api/rate-templates", payload);
  return data;
}

export async function deleteRateTemplate(id: number): Promise<void> {
  await api.delete(`/api/rate-templates/${id}`);
}

// ===================== 拟人化 humanize =====================
// 后端是 PUT 但语义是 PATCH（仅传非空字段，未传字段保持不变）
export async function getHumanize(aid: number): Promise<HumanizeConfig> {
  const { data } = await api.get<HumanizeConfig>(
    `/api/accounts/${aid}/humanize`,
  );
  return data;
}

export async function patchHumanize(
  aid: number,
  body: HumanizeUpdate,
): Promise<HumanizeConfig> {
  const { data } = await api.put<HumanizeConfig>(
    `/api/accounts/${aid}/humanize`,
    body,
  );
  return data;
}

// ===================== 系统健康概览（Dashboard 用）=====================
export async function getHealthOverview(): Promise<HealthOverview> {
  const { data } = await api.get<HealthOverview>("/api/system/health-overview");
  return data;
}
