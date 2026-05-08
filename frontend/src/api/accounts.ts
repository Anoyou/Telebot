// 账号 API 包装
import { api } from "@/lib/api";
import type {
  AccountConfirm2FARequest,
  AccountConfirmCodeRequest,
  AccountConfirmResponse,
  AccountDetail,
  AccountStartLoginRequest,
  AccountStartLoginResponse,
  AccountSummary,
  AccountUpdateRequest,
  AccountFeatureItem,
} from "@/api/types";

export async function listAccounts(): Promise<AccountSummary[]> {
  const { data } = await api.get<AccountSummary[]>("/api/accounts");
  return data;
}

// 头像 URL：直接拼成相对路径，给 <img src> 用；后端 24h 私有缓存 + 不存在时 404
// 调用方拿到 404 后由 AccountAvatar 自动 fallback 到首字母
export function avatarUrl(aid: number): string {
  const base = (api.defaults.baseURL || "").replace(/\/$/, "");
  return `${base}/api/accounts/${aid}/avatar`;
}

export async function getAccount(aid: number): Promise<AccountDetail> {
  const { data } = await api.get<AccountDetail>(`/api/accounts/${aid}`);
  return data;
}

export async function patchAccount(
  aid: number,
  payload: AccountUpdateRequest,
): Promise<AccountDetail> {
  const { data } = await api.patch<AccountDetail>(`/api/accounts/${aid}`, payload);
  return data;
}

export async function deleteAccount(aid: number): Promise<void> {
  await api.delete(`/api/accounts/${aid}`);
}

export async function pauseAccount(aid: number): Promise<void> {
  await api.post(`/api/accounts/${aid}/pause`);
}

export async function resumeAccount(aid: number): Promise<void> {
  await api.post(`/api/accounts/${aid}/resume`);
}

// ===================== 绑定向导 =====================
export async function loginStart(
  payload: AccountStartLoginRequest,
): Promise<AccountStartLoginResponse> {
  const { data } = await api.post<AccountStartLoginResponse>(
    "/api/accounts/login/start",
    payload,
  );
  return data;
}

export async function loginCode(
  payload: AccountConfirmCodeRequest,
): Promise<AccountConfirmResponse> {
  const { data } = await api.post<AccountConfirmResponse>(
    "/api/accounts/login/code",
    payload,
  );
  return data;
}

export async function login2fa(
  payload: AccountConfirm2FARequest,
): Promise<AccountConfirmResponse> {
  const { data } = await api.post<AccountConfirmResponse>(
    "/api/accounts/login/2fa",
    payload,
  );
  return data;
}

export async function cloneConfig(
  toAid: number,
  fromAid: number,
  features: string[],
): Promise<void> {
  await api.post(`/api/accounts/${toAid}/clone-config`, {
    from_account_id: fromAid,
    features,
  });
}

// ===================== 插件启停 =====================
export async function listAccountFeatures(
  aid: number,
): Promise<AccountFeatureItem[]> {
  const { data } = await api.get<AccountFeatureItem[]>(
    `/api/accounts/${aid}/features`,
  );
  return data;
}

export async function toggleAccountFeature(
  aid: number,
  key: string,
  enabled: boolean,
): Promise<void> {
  await api.patch(`/api/accounts/${aid}/features/${key}`, { enabled });
}
