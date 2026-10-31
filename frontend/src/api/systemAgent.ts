/** System Agent API client. */
import type { components } from "@/api/schema";
import { api, apiFetch } from "@/lib/api";
import { NdjsonDecoder } from "@/lib/ndjsonStream";

export interface SystemAgentConfig {
  enabled: boolean;
  provider_id: number | null;
  model: string | null;
  fallback_provider_ids: number[];
  require_tool_approval: boolean;
  max_steps: number;
  max_tool_calls: number;
  session_token_limit: number;
}

export interface SystemAgentCapability {
  name: string;
  description: string;
  read_only: boolean;
  min_role: string;
  risk: string;
  channels: string[];
  available: boolean;
  unavailable_reason?: string | null;
  /** builtin | plugin */
  source?: string | null;
  plugin_key?: string | null;
}

export interface SystemAgentModelMatrixItem {
  provider_id: number;
  provider_name: string;
  model: string;
  enabled?: boolean;
  declared_supports_tools?: boolean | null;
  declared_supports_images?: boolean | null;
  declared_reasoning_efforts?: unknown;
  probed_supports_tools?: boolean | null;
  probed_status?: string | null;
  health?: {
    state?: string;
    cooldown_remaining_seconds?: number;
    last_error_class?: string | null;
    last_error_message?: string | null;
  };
}

export interface SystemAgentCapabilities {
  enabled: boolean;
  provider_id: number | null;
  model: string | null;
  provider_name?: string | null;
  resolved_model?: string | null;
  ai_enabled: boolean;
  timezone: string;
  tools: SystemAgentCapability[];
  stage: number;
  write_tools_available: boolean;
  model_matrix?: SystemAgentModelMatrixItem[];
}

export interface SystemAgentSession {
  id: string;
  web_user_id: number | null;
  bot_tg_user_id: number | null;
  account_id: number | null;
  channel: string;
  title: string | null;
  /** interactive = 对话；scheduled = 定时任务落轨迹 */
  origin?: "interactive" | "scheduled" | string;
  status: string;
  memory_summary?: string;
  memory_state?: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
  /** 仅新建会话响应（第 20 轮）：因 default_account 且服务器只有 1 个账号而自动关联。 */
  account_auto_linked?: boolean;
  /** 第 22 轮：本会话上次选择的本轮模型；null = 从未选择（按自动）。旧服务器没有该字段。 */
  model_selection?: SystemAgentModelSelection | null;
}

export interface SystemAgentMessage {
  id: number;
  session_id: string;
  role: string;
  content: Record<string, unknown>;
  usage?: Record<string, unknown> | null;
  run_status?: "pending" | "succeeded" | "failed" | "completed" | string;
  error_code?: string | null;
  error_message?: string | null;
  retry_count?: number;
  created_at: string | null;
}

export interface SystemAgentImageAttachment {
  kind: "image";
  source: "data_url" | "remote_url";
  mime_type?: string | null;
  url?: string | null;
  data_url?: string | null;
  name?: string | null;
}

export interface SystemAgentProviderSwitchCandidate {
  provider_id: number;
  provider_name: string;
  model: string;
}

export interface SystemAgentProviderSwitch {
  from_provider_name?: string;
  candidates: SystemAgentProviderSwitchCandidate[];
}

export interface SystemAgentToolApprovalItem {
  name: string;
  description: string;
  read_only: boolean;
  risk: string;
  call_id?: string;
  arguments?: Record<string, unknown>;
}

export interface SystemAgentToolApproval {
  domains?: string[];
  calls?: Array<{ call_id: string; name: string; arguments?: Record<string, unknown> }>;
  tools: SystemAgentToolApprovalItem[];
}

export interface SystemAgentAction {
  id: string;
  session_id?: string | null;
  account_id?: number | null;
  channel: string;
  tool_name: string;
  arguments?: Record<string, unknown>;
  secret_fields?: string[] | null;
  has_secret?: boolean;
  summary: string;
  preview: Record<string, unknown>;
  risk: string;
  status: string;
  result?: Record<string, unknown> | null;
  error_code?: string | null;
  error_message?: string | null;
  runtime_sync_status?: string;
  runtime_sync_error?: string | null;
  runtime_retryable?: boolean;
  expires_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  executed_at?: string | null;
  session_title?: string | null;
  session_origin?: string | null;
}

export type SystemAgentStreamEvent = {
  type: string;
  run_id?: string;
  session_id?: string;
  seq?: number;
  ts?: string;
  message_id?: number;
  content?: string;
  reasoning?: string;
  delta?: string;
  message?: string;
  code?: string;
  status_code?: number | null;
  error_category?: string | null;
  suggestion?: string | null;
  upstream_status_code?: number | null;
  upstream_error_code?: string | null;
  upstream_error_message?: string | null;
  upstream_error_detail?: string | null;
  upstream_request_id?: string | null;
  client_request_id?: string | null;
  gateway_request_id?: string | null;
  tool_name?: string;
  call_id?: string;
  is_error?: boolean;
  result_summary?: unknown;
  images?: SystemAgentImageAttachment[];
  arguments_summary?: unknown;
  usage?: Record<string, unknown>;
  ok?: boolean;
  hint?: { web_path?: string; message?: string };
  provider_id?: number;
  provider_name?: string;
  model?: string;
  reason?: string;
  provider_switch?: SystemAgentProviderSwitch;
  tool_approval?: SystemAgentToolApproval;
  action?: SystemAgentAction;
  stream_fallback?: boolean;
  [key: string]: unknown;
};

export interface SystemAgentRun {
  id: string;
  run_id: string;
  session_id: string;
  web_user_id: number | null;
  bot_tg_user_id?: number | null;
  channel?: "web" | "bot" | string;
  pending_turn_id?: string | null;
  user_message_id: number | null;
  client_request_id: string;
  kind: "message" | "retry" | string;
  status:
    | "queued"
    | "running"
    | "waiting_input"
    | "waiting_approval"
    | "succeeded"
    | "failed"
    | "cancelled"
    | string;
  phase?: string;
  /** 排在该任务前面的任务数（执行中的任务算 1 个）；只在 queued 且确实需要等待时有值。 */
  queue_ahead?: number | null;
  paused_reason?: string | null;
  usage?: Record<string, unknown> | null;
  elapsed_ms?: number | null;
  last_seq: number;
  cancel_requested: boolean;
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SystemAgentQueueItem {
  id: string;
  session_id: string;
  run_id?: string | null;
  web_user_id?: number | null;
  bot_tg_user_id?: number | null;
  channel: "web" | "bot" | string;
  kind: string;
  position: number;
  status: "pending" | "dispatching" | "paused" | string;
  blocked_reason?: string | null;
  content: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SystemAgentRunInput {
  id: number;
  run_id: string;
  kind: string;
  status: string;
  client_request_id: string;
  created_at?: string | null;
  applied_at?: string | null;
}

export async function getSystemAgentConfig(): Promise<SystemAgentConfig> {
  const { data } = await api.get<SystemAgentConfig>("/api/system-agent/config");
  return data;
}

export async function patchSystemAgentConfig(
  patch: Partial<SystemAgentConfig>,
): Promise<SystemAgentConfig> {
  const { data } = await api.patch<SystemAgentConfig>("/api/system-agent/config", patch);
  return data;
}

export async function getSystemAgentCapabilities(): Promise<SystemAgentCapabilities> {
  const { data } = await api.get<SystemAgentCapabilities>("/api/system-agent/capabilities");
  return data;
}

export async function listSystemAgentRunEvents(
  runId: string,
  afterSeq = 0,
  limit = 500,
): Promise<SystemAgentStreamEvent[]> {
  const { data } = await api.get<Array<{ run_id: string; seq: number; event: Record<string, unknown>; created_at?: string | null }>>(
    `/api/system-agent/runs/${runId}/events`,
    { params: { after_seq: afterSeq, limit } },
  );
  return (data || []).map((row) => ({
    ...(row.event || {}),
    type: String(row.event?.type || ""),
    run_id: row.run_id,
    seq: row.seq,
    created_at: row.created_at ?? null,
  })) as SystemAgentStreamEvent[];
}

export async function listSystemAgentRuns(params?: {
  status?: string;
  since?: string;
  until?: string;
  limit?: number;
  include_bot?: boolean;
  /** 只返回指定会话的任务。 */
  session_id?: string;
}): Promise<SystemAgentRun[]> {
  const { data } = await api.get<SystemAgentRun[]>("/api/system-agent/runs", { params });
  return data;
}

export async function listSystemAgentQueue(params?: {
  session_id?: string;
  include_bot?: boolean;
}): Promise<SystemAgentQueueItem[]> {
  const { data } = await api.get<SystemAgentQueueItem[]>("/api/system-agent/queue", {
    params,
  });
  return data;
}

export async function updateSystemAgentQueueItem(
  turnId: string,
  payload: { content?: string; pinned?: boolean },
): Promise<SystemAgentQueueItem> {
  const { data } = await api.patch<SystemAgentQueueItem>(
    `/api/system-agent/queue/${turnId}`,
    payload,
  );
  return data;
}

export async function deleteSystemAgentQueueItem(turnId: string): Promise<SystemAgentRun> {
  const { data } = await api.delete<SystemAgentRun>(`/api/system-agent/queue/${turnId}`);
  return data;
}

export async function steerSystemAgentQueueItem(
  turnId: string,
  payload: { client_request_id: string },
): Promise<SystemAgentRunInput> {
  const { data } = await api.post<SystemAgentRunInput>(
    `/api/system-agent/queue/${turnId}/steer`,
    payload,
  );
  return data;
}

export async function reorderSystemAgentQueue(
  sessionId: string,
  turnIds: string[],
): Promise<SystemAgentQueueItem[]> {
  const { data } = await api.post<SystemAgentQueueItem[]>(
    `/api/system-agent/sessions/${sessionId}/queue/reorder`,
    { turn_ids: turnIds },
  );
  return data;
}

export async function clearSystemAgentQueue(sessionId: string): Promise<number> {
  const { data } = await api.delete<{ count: number }>(
    `/api/system-agent/sessions/${sessionId}/queue`,
  );
  return Number(data.count || 0);
}

export async function resumeSystemAgentQueue(sessionId: string): Promise<number> {
  const { data } = await api.post<{ count: number }>(
    `/api/system-agent/sessions/${sessionId}/queue/resume`,
  );
  return Number(data.count || 0);
}

export interface SystemAgentUserMemory {
  id: number;
  scope_type: string;
  scope_id: number;
  content: string;
  source: string;
  enabled: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export async function listSystemAgentUserMemory(): Promise<SystemAgentUserMemory[]> {
  const { data } = await api.get<SystemAgentUserMemory[]>("/api/system-agent/memory");
  return data;
}

export async function createSystemAgentUserMemory(payload: {
  content: string;
  enabled?: boolean;
}): Promise<SystemAgentUserMemory> {
  const { data } = await api.post<SystemAgentUserMemory>("/api/system-agent/memory", payload);
  return data;
}

export async function patchSystemAgentUserMemory(
  id: number,
  payload: { content?: string; enabled?: boolean },
): Promise<SystemAgentUserMemory> {
  const { data } = await api.patch<SystemAgentUserMemory>(`/api/system-agent/memory/${id}`, payload);
  return data;
}

export async function deleteSystemAgentUserMemory(id: number): Promise<void> {
  await api.delete(`/api/system-agent/memory/${id}`);
}

export async function listSystemAgentSessions(
  params?: {
    status?: string;
    origin?: string;
    include_bot?: boolean;
    limit?: number;
    /** 只取列表摘要（不含 memory_summary / memory_state），轮询会话列表时减少传输量。 */
    compact?: boolean;
  },
): Promise<SystemAgentSession[]> {
  const { data } = await api.get<SystemAgentSession[]>("/api/system-agent/sessions", { params });
  return data;
}

export async function createSystemAgentSession(payload?: {
  account_id?: number | null;
  title?: string;
  /** 第 20 轮：未指定账号时只有 1 个账号则自动关联（旧服务器忽略）。 */
  default_account?: boolean;
  /** 第 22 轮：新会话初始的模型选择（草稿里选过模型再发送时带上；旧服务器忽略）。 */
  model_selection?: SystemAgentModelSelection | null;
}): Promise<SystemAgentSession> {
  const { data } = await api.post<SystemAgentSession>("/api/system-agent/sessions", payload || {});
  return data;
}

export async function getSystemAgentSession(sessionId: string): Promise<SystemAgentSession> {
  const { data } = await api.get<SystemAgentSession>(`/api/system-agent/sessions/${sessionId}`);
  return data;
}

export async function updateSystemAgentSession(
  sessionId: string,
  payload: {
    title?: string;
    status?: string;
    account_id?: number | null;
    /** 第 22 轮：记住本会话的模型选择；null 清除（= 自动）。只改它不刷新会话排序。 */
    model_selection?: SystemAgentModelSelection | null;
  },
): Promise<SystemAgentSession> {
  const { data } = await api.patch<SystemAgentSession>(
    `/api/system-agent/sessions/${sessionId}`,
    payload,
  );
  return data;
}

export async function deleteSystemAgentSession(sessionId: string): Promise<void> {
  await api.delete(`/api/system-agent/sessions/${sessionId}`);
}

export async function listSystemAgentMessages(
  sessionId: string,
  params?: { limit?: number; before_id?: number },
): Promise<SystemAgentMessage[]> {
  const { data } = await api.get<SystemAgentMessage[]>(
    `/api/system-agent/sessions/${sessionId}/messages`,
    { params },
  );
  return data;
}

/** 本轮模型选择：auto 走全局配置；pinned 固定 provider+model（不改全局） */
export type SystemAgentModelSelection =
  | {
      mode: "auto";
      execution_backend?: "provider" | "direct" | "codex_gateway";
      client_identity_profile?: string | null;
    }
  | {
      mode: "pinned";
      provider_id: number;
      model: string;
      execution_backend?: "provider" | "direct" | "codex_gateway";
      client_identity_profile?: string | null;
    };

export async function startSystemAgentRun(
  sessionId: string,
  payload: {
    content: string;
    attachments?: SystemAgentImageAttachment[];
    account_id?: number | null;
    client_request_id: string;
    model_selection?: SystemAgentModelSelection | null;
  },
): Promise<SystemAgentRun> {
  const { data } = await api.post<SystemAgentRun>(
    `/api/system-agent/sessions/${sessionId}/runs`,
    payload,
  );
  return data;
}

export async function startSystemAgentRetryRun(
  sessionId: string,
  messageId: number,
  payload: {
    account_id?: number | null;
    fallback_provider_id?: number | null;
    approved_tools?: string[];
    client_request_id: string;
    model_selection?: SystemAgentModelSelection | null;
    attachments?: SystemAgentImageAttachment[];
  },
): Promise<SystemAgentRun> {
  const { data } = await api.post<SystemAgentRun>(
    `/api/system-agent/sessions/${sessionId}/messages/${messageId}/retry/runs`,
    payload,
  );
  return data;
}

export async function startSystemAgentRegenerateRun(
  sessionId: string,
  messageId: number,
  payload: {
    assistant_message_id: number;
    content?: string | null;
    account_id?: number | null;
    fallback_provider_id?: number | null;
    approved_tools?: string[];
    client_request_id: string;
    model_selection?: SystemAgentModelSelection | null;
    attachments?: SystemAgentImageAttachment[];
  },
): Promise<SystemAgentRun> {
  const { data } = await api.post<SystemAgentRun>(
    `/api/system-agent/sessions/${sessionId}/messages/${messageId}/regenerate/runs`,
    payload,
  );
  return data;
}

export async function getSystemAgentRun(runId: string): Promise<SystemAgentRun> {
  const { data } = await api.get<SystemAgentRun>(`/api/system-agent/runs/${runId}`);
  return data;
}

export async function cancelSystemAgentRun(runId: string): Promise<SystemAgentRun> {
  const { data } = await api.post<SystemAgentRun>(`/api/system-agent/runs/${runId}/cancel`);
  return data;
}

export async function steerSystemAgentRun(
  runId: string,
  payload: {
    content?: string;
    attachments?: SystemAgentImageAttachment[];
    client_request_id: string;
  },
): Promise<SystemAgentRunInput> {
  const { data } = await api.post<SystemAgentRunInput>(
    `/api/system-agent/runs/${runId}/steer`,
    payload,
  );
  return data;
}

export async function submitSystemAgentRunInput(
  runId: string,
  payload: {
    content?: string;
    fallback_provider_id?: number | null;
    client_request_id: string;
  },
): Promise<SystemAgentRunInput> {
  const { data } = await api.post<SystemAgentRunInput>(
    `/api/system-agent/runs/${runId}/input`,
    payload,
  );
  return data;
}

export async function approveSystemAgentRun(
  runId: string,
  payload: {
    approved: boolean;
    approved_tools?: string[];
    content?: string;
    client_request_id: string;
  },
): Promise<SystemAgentRunInput> {
  const { data } = await api.post<SystemAgentRunInput>(
    `/api/system-agent/runs/${runId}/approval`,
    payload,
  );
  return data;
}

export async function stopAndReplaceSystemAgentRun(
  runId: string,
  payload: {
    content?: string;
    attachments?: SystemAgentImageAttachment[];
    client_request_id: string;
    model_selection?: SystemAgentModelSelection | null;
  },
): Promise<SystemAgentRun> {
  const { data } = await api.post<SystemAgentRun>(
    `/api/system-agent/runs/${runId}/stop-and-replace`,
    payload,
  );
  return data;
}

/** 流空闲超时（90s 无任何字节）主动断开；后端心跳帧会把这种情形压到极罕见，
 * 触发时按"核对状态"静默处理，不当成连接错误提示用户。 */
export class IdleTimeoutError extends Error {
  constructor() {
    super("进度连接长时间无响应，正在核对任务状态。");
    this.name = "IdleTimeoutError";
  }
}

export async function streamSystemAgentRun(
  runId: string,
  afterSeq: number,
  onEvent: (event: SystemAgentStreamEvent) => void,
  opts?: { signal?: AbortSignal },
): Promise<void> {
  const params = new URLSearchParams({ after_seq: String(Math.max(0, afterSeq)) });
  // 只重建 GET 订阅，不取消后台任务，更不能重发创建 Run 的 POST。
  const controller = new AbortController();
  const abort = () => controller.abort(opts?.signal?.reason);
  if (opts?.signal?.aborted) abort();
  else opts?.signal?.addEventListener("abort", abort, { once: true });
  let idleTimer: ReturnType<typeof setTimeout> | undefined;
  const activity = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      controller.abort(new IdleTimeoutError());
    }, 90_000);
  };
  activity();
  try {
    const response = await apiFetch(`/api/system-agent/runs/${runId}/stream?${params}`, {
      method: "GET",
      headers: { Accept: "application/x-ndjson" },
      signal: controller.signal,
    });
    await consumeSystemAgentStream(response, onEvent, activity);
  } finally {
    clearTimeout(idleTimer);
    opts?.signal?.removeEventListener("abort", abort);
  }
}

function streamErrorMessage(value: unknown, fallback: string): string {
  if (!value || typeof value !== "object") return fallback;
  const obj = value as Record<string, unknown>;
  const error = obj.error as Record<string, unknown> | undefined;
  const detail = obj.detail as Record<string, unknown> | string | undefined;
  if (error?.message && typeof error.message === "string") return error.message;
  if (detail && typeof detail === "object" && typeof detail.message === "string") {
    return detail.message;
  }
  if (typeof detail === "string") return detail;
  return fallback;
}

export async function listSystemAgentActions(params?: {
  session_id?: string;
  status?: string;
  limit?: number;
}): Promise<SystemAgentAction[]> {
  const { data } = await api.get<SystemAgentAction[]>("/api/system-agent/actions", { params });
  return data;
}

export async function getSystemAgentAction(actionId: string): Promise<SystemAgentAction> {
  const { data } = await api.get<SystemAgentAction>(`/api/system-agent/actions/${actionId}`);
  return data;
}

export async function confirmSystemAgentAction(actionId: string): Promise<{
  ok: boolean;
  already_final?: boolean;
  error_code?: string | null;
  error_message?: string | null;
  action?: SystemAgentAction | null;
}> {
  const { data } = await api.post(`/api/system-agent/actions/${actionId}/confirm`);
  return data;
}

export async function rejectSystemAgentAction(actionId: string): Promise<SystemAgentAction> {
  const { data } = await api.post<SystemAgentAction>(`/api/system-agent/actions/${actionId}/reject`);
  return data;
}

export async function retrySystemAgentRuntimeSync(actionId: string): Promise<{
  ok: boolean;
  error_code?: string | null;
  error_message?: string | null;
  action?: SystemAgentAction | null;
}> {
  const { data } = await api.post(`/api/system-agent/actions/${actionId}/retry-runtime-sync`);
  return data;
}

/** 消费 NDJSON 对话流；任意分块边界安全。 */
export async function streamSystemAgentMessage(
  sessionId: string,
  payload: { content: string; account_id?: number | null },
  onEvent: (event: SystemAgentStreamEvent) => void,
  opts?: { signal?: AbortSignal },
): Promise<void> {
  return streamSystemAgentRequest(
    `/api/system-agent/sessions/${sessionId}/messages/stream`,
    payload,
    onEvent,
    opts,
  );
}

export async function retrySystemAgentMessage(
  sessionId: string,
  messageId: number,
  payload: {
    account_id?: number | null;
    fallback_provider_id?: number | null;
    approved_tools?: string[];
  },
  onEvent: (event: SystemAgentStreamEvent) => void,
  opts?: { signal?: AbortSignal },
): Promise<void> {
  return streamSystemAgentRequest(
    `/api/system-agent/sessions/${sessionId}/messages/${messageId}/retry/stream`,
    payload,
    onEvent,
    opts,
  );
}

async function streamSystemAgentRequest(
  path: string,
  payload: Record<string, unknown>,
  onEvent: (event: SystemAgentStreamEvent) => void,
  opts?: { signal?: AbortSignal },
): Promise<void> {
  const response = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
    body: JSON.stringify(payload),
    signal: opts?.signal,
  });
  return consumeSystemAgentStream(response, onEvent);
}

async function consumeSystemAgentStream(
  response: Response,
  onEvent: (event: SystemAgentStreamEvent) => void,
  onActivity?: () => void,
): Promise<void> {
  if (!response.ok) {
    let payloadError: unknown;
    try {
      payloadError = await response.json();
    } catch {
      payloadError = null;
    }
    throw new Error(streamErrorMessage(payloadError, `助手请求失败（HTTP ${response.status}）`));
  }
  if (!response.body) throw new Error("浏览器没有提供可读取的流式响应。");

  const reader = response.body.getReader();
  const decoder = new NdjsonDecoder<SystemAgentStreamEvent>();
  let doneReceived = false;
  let reachedEof = false;
  const consumeEvent = (event: SystemAgentStreamEvent) => {
    if (event.type === "done") doneReceived = true;
    onEvent(event);
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (value?.byteLength) onActivity?.();
      decoder.push(value).forEach(consumeEvent);
      reachedEof = done;
      // done 已是应用层终态，不能继续等代理或浏览器关闭连接。
      if (done || doneReceived) break;
    }
    if (reachedEof) decoder.finish().forEach(consumeEvent);
    if (!doneReceived) throw new Error("流式响应提前结束，没有返回最终状态。");
  } finally {
    if (!reachedEof) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

// ── 一键体检（与助手“体检一下”同一个只读工具，不经过模型） ────────────────
export type SystemCheckup = components["schemas"]["SystemCheckupOut"];
export type SystemCheckupSection = components["schemas"]["SystemCheckupSectionOut"];
export type SystemCheckupStep = components["schemas"]["SystemCheckupStepOut"];

/** 服务器顺序跑 10 项只读检查，总预算约 25 秒，放宽默认 15 秒超时。 */
export async function runSystemCheckup(hours = 24): Promise<SystemCheckup> {
  const { data } = await api.get<SystemCheckup>("/api/system-agent/checkup", {
    params: { hours },
    timeout: 60_000,
  });
  return data;
}
