// 代理 API 包装
import { api } from "@/lib/api";
import type {
  ProxyCreate,
  ProxyOut,
  ProxyTestResult,
  ProxyUpdate,
  ProxyUsageResponse,
} from "@/api/types";

export async function listProxies(): Promise<ProxyOut[]> {
  const { data } = await api.get<ProxyOut[]>("/api/proxies");
  return data;
}

export async function createProxy(payload: ProxyCreate): Promise<ProxyOut> {
  const { data } = await api.post<ProxyOut>("/api/proxies", payload);
  return data;
}

export async function patchProxy(
  id: number,
  payload: ProxyUpdate,
): Promise<ProxyOut> {
  const { data } = await api.patch<ProxyOut>(`/api/proxies/${id}`, payload);
  return data;
}

export async function deleteProxy(id: number): Promise<void> {
  await api.delete(`/api/proxies/${id}`);
}

export async function testProxy(id: number): Promise<ProxyTestResult> {
  const { data } = await api.post<ProxyTestResult>(`/api/proxies/${id}/test`);
  return data;
}

/** 反查：哪些 account / llm_provider 引用了这条代理。
 *  用于代理管理页"被谁用了"展开 + 删除前的影响面提示。 */
export async function getProxyUsage(id: number): Promise<ProxyUsageResponse> {
  const { data } = await api.get<ProxyUsageResponse>(`/api/proxies/${id}/usage`);
  return data;
}
