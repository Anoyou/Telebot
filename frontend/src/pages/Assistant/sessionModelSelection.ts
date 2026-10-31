/**
 * Agent 每个会话记住上次的本轮模型选择（第 22 轮）。
 *
 * - 以服务器会话上的 `model_selection` 为准（Web 与 iOS 一致、换设备也记得）；
 * - 服务器没有值时退回本机按会话 id 记的值（升级前选过的、或旧服务器）；
 * - 新会话 / 尚未建会话的草稿默认「自动」；不再把上一个会话的选择带到别的会话；
 * - 所选模型已被删除或停用时回落「自动」（`resolveAvailableSelection`），不报错。
 *
 * 不写全局 Provider 配置。
 */

import type { SystemAgentModelSelection } from "@/api/systemAgent";
import type {
  AgentClientIdentity,
  AgentExecutionBackend,
} from "@/lib/assistantClientSelection";

export type SessionModelSelection =
  | {
      mode: "auto";
      executionBackend: SessionExecutionBackend;
      clientIdentityProfile?: SessionClientIdentity;
    }
  | {
      mode: "pinned";
      providerId: number;
      model: string;
      executionBackend: SessionExecutionBackend;
      clientIdentityProfile?: SessionClientIdentity;
    };

export type SessionExecutionBackend = AgentExecutionBackend;
export type SessionClientIdentity = AgentClientIdentity;

export const DEFAULT_SESSION_MODEL_SELECTION: SessionModelSelection = {
  mode: "auto",
  executionBackend: "provider",
};

const CLIENT_IDENTITIES = new Set<SessionClientIdentity>([
  "auto",
  "minimal",
  "openai_sdk",
  "codex_tui",
  "codex_desktop",
  "claude_code",
  "claude_desktop",
  "grok_cli",
]);

const STORAGE_KEY = "telepilot.system-agent.session-model.v1";
const LAST_SELECTION_KEY = "telepilot.system-agent.last-model-selection.v1";

type Store = Record<string, SessionModelSelection>;

function readStore(): Store {
  try {
    const raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}") as unknown;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    return raw as Store;
  } catch {
    return {};
  }
}

function writeStore(store: Store): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  } catch {
    // ignore
  }
}

function normalizeSelection(value: unknown): SessionModelSelection | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const candidate = value as Partial<SessionModelSelection>;
  const executionBackend = ["provider", "direct", "codex_gateway"].includes(
    String(candidate.executionBackend || ""),
  )
    ? candidate.executionBackend as SessionExecutionBackend
    : "provider";
  const clientIdentityProfile = CLIENT_IDENTITIES.has(candidate.clientIdentityProfile)
    ? candidate.clientIdentityProfile
    : undefined;
  // Agent 快速选择器不再暴露 minimal / openai_sdk。它们仍是 Provider
  // 配置和后端兼容契约的一部分；旧选择恢复时统一回到标准 API 的自动身份。
  const visibleClientIdentityProfile = clientIdentityProfile === "minimal"
    || clientIdentityProfile === "openai_sdk"
    ? "auto"
    : clientIdentityProfile;
  const common = visibleClientIdentityProfile
    ? { executionBackend, clientIdentityProfile: visibleClientIdentityProfile }
    : { executionBackend };
  if (
    candidate.mode === "pinned"
    && Number(candidate.providerId) > 0
    && typeof candidate.model === "string"
    && candidate.model
  ) {
    return {
      mode: "pinned",
      providerId: Number(candidate.providerId),
      model: candidate.model,
      ...common,
    };
  }
  if (candidate.mode === "auto") return { mode: "auto", ...common };
  return null;
}

function clearLegacyLastSelection(): void {
  // 第 22 轮前「最近一次选择」对所有会话生效；改为按会话记忆后清掉旧键，避免误读。
  try {
    window.localStorage.removeItem(LAST_SELECTION_KEY);
  } catch {
    // ignore
  }
}

/** 服务器会话上的 `model_selection`（API 格式）→ 界面格式；无值或格式不对时为 null。 */
export function fromApiModelSelection(
  value: SystemAgentModelSelection | null | undefined,
): SessionModelSelection | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  return normalizeSelection({
    mode: raw.mode,
    providerId: raw.provider_id,
    model: raw.model,
    executionBackend: raw.execution_backend,
    clientIdentityProfile: raw.client_identity_profile ?? undefined,
  });
}

/**
 * 打开会话时恢复它上次的选择：服务器值优先，其次本机按会话 id 记的值，否则「自动」。
 * `sessionId` 为空（新会话草稿）时总是「自动」。
 */
export function loadSessionModelSelection(
  sessionId: string | null | undefined,
  serverSelection?: SystemAgentModelSelection | null,
): SessionModelSelection {
  if (!sessionId) return DEFAULT_SESSION_MODEL_SELECTION;
  return fromApiModelSelection(serverSelection)
    || normalizeSelection(readStore()[sessionId])
    || DEFAULT_SESSION_MODEL_SELECTION;
}

/** 在本机按会话 id 记住选择（服务器值的兜底）；没有会话 id 时不记。 */
export function saveSessionModelSelection(
  sessionId: string | null | undefined,
  selection: SessionModelSelection,
): void {
  clearLegacyLastSelection();
  if (!sessionId) return;
  const store = readStore();
  if (isDefaultSelection(selection)) {
    delete store[sessionId];
  } else {
    store[sessionId] = selection;
  }
  writeStore(store);
}

export function isDefaultSelection(selection: SessionModelSelection): boolean {
  return selection.mode === "auto"
    && selection.executionBackend === "provider"
    && !selection.clientIdentityProfile;
}

/**
 * 恢复出的固定模型已不在可选列表里（被删除 / 停用 / 不再支持工具调用）时回落「自动」。
 * `available` 为 null 表示模型列表还没读到：先原样保留，读到后再判断。
 */
export function resolveAvailableSelection(
  selection: SessionModelSelection,
  available: ReadonlyArray<{ providerId: number; model: string }> | null,
): SessionModelSelection {
  if (selection.mode !== "pinned" || available == null) return selection;
  const found = available.some(
    (item) => item.providerId === selection.providerId && item.model === selection.model,
  );
  return found ? selection : DEFAULT_SESSION_MODEL_SELECTION;
}

export function toApiModelSelection(
  selection: SessionModelSelection,
): SystemAgentModelSelection {
  const common = {
    execution_backend: selection.executionBackend,
    client_identity_profile: selection.clientIdentityProfile,
  };
  if (selection.mode === "pinned") {
    return {
      mode: "pinned",
      provider_id: selection.providerId,
      model: selection.model,
      ...common,
    };
  }
  return { mode: "auto", ...common };
}
