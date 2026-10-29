// 插件页面 Bridge 协议 v1（schemas/plugin-ui-widgets.json → pages.bridge），Web 与 iOS 同一套：
// 页面 → 宿主 {tp:"telepilot", v:1, id, method, params}
// 宿主 → 页面 {tp:"telepilot", v:1, id, ok, result | error:{code, message}}
// 宿主事件   {tp:"telepilot", v:1, event:"theme", data:{scheme}}
// 宿主只做格式校验与转发，权限由服务器按页面声明逐次校验（POST /api/plugin-ui/pages/{key}/{page}/bridge）。

export const BRIDGE_PROTOCOL = 1;
const METHOD_RE = /^[a-z][a-z0-9_.]{0,63}$/;
const ID_RE = /^[A-Za-z0-9_.:-]{1,64}$/;
/** 单次请求参数上限（与服务器 payload ≤16KB 同一量级，宿主先挡掉明显过大的请求）。 */
export const MAX_PARAMS_BYTES = 32_768;

export interface BridgeRequest {
  id: string;
  method: string;
  params: Record<string, unknown>;
}

export interface BridgeReply {
  tp: "telepilot";
  v: number;
  id: string;
  ok: boolean;
  result?: unknown;
  error?: { code: string; message: string };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

/** 解析页面发来的消息；不是 Bridge 请求（或格式不合法）时返回 null，宿主直接忽略。 */
export function parseBridgeRequest(data: unknown): BridgeRequest | null {
  if (!isRecord(data) || data.tp !== "telepilot" || data.v !== BRIDGE_PROTOCOL) return null;
  if (typeof data.id !== "string" || !ID_RE.test(data.id)) return null;
  if (typeof data.method !== "string" || !METHOD_RE.test(data.method)) return null;
  const params = data.params === undefined ? {} : data.params;
  if (!isRecord(params)) return null;
  try {
    if (JSON.stringify(params).length > MAX_PARAMS_BYTES) return null;
  } catch {
    return null;
  }
  return { id: data.id, method: data.method, params };
}

export function bridgeOk(id: string, result: unknown): BridgeReply {
  return { tp: "telepilot", v: BRIDGE_PROTOCOL, id, ok: true, result: result ?? null };
}

export function bridgeError(id: string, code: string, message: string): BridgeReply {
  return { tp: "telepilot", v: BRIDGE_PROTOCOL, id, ok: false, error: { code, message } };
}

export function themeEvent(scheme: "light" | "dark") {
  return { tp: "telepilot" as const, v: BRIDGE_PROTOCOL, event: "theme" as const, data: { scheme } };
}

/** 宿主本地回答的方法（不经服务器）。 */
export function localBridgeResult(method: string, scheme: "light" | "dark"): { handled: boolean; result?: unknown } {
  if (method === "theme.get") return { handled: true, result: { scheme } };
  return { handled: false };
}

/** iframe 沙箱：只允许脚本，不给 allow-same-origin（拿不到宿主 Cookie / localStorage / token），不允许表单、弹窗、顶层跳转。 */
export const PAGE_SANDBOX = "allow-scripts";
