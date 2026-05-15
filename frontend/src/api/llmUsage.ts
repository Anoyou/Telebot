import { api } from "@/lib/api";

export interface LLMUsageRecord {
  id: number;
  account_id: number | null;
  provider_id: number | null;
  provider_name?: string | null;
  model: string | null;
  source?: string | null;
  input_tokens: number;
  output_tokens: number;
  latency_ms?: number | null;
  success: boolean;
  error_type?: string | null;
  used_fallback?: boolean;
  created_at: string;
}

export interface LLMUsageRecentResponse {
  items: LLMUsageRecord[];
}

/**
 * 轻量探测：当前后端版本可能尚未开放该接口。
 * 若返回 404，由页面层展示温和空状态，不中断其它 AI 页面。
 */
export async function listRecentLLMUsage(limit = 20): Promise<LLMUsageRecord[]> {
  const { data } = await api.get<LLMUsageRecentResponse | LLMUsageRecord[]>(
    "/api/llm/usage/recent",
    { params: { limit } },
  );

  if (Array.isArray(data)) return data;
  return data.items || [];
}
