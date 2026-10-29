// axios 客户端：携带 cookie；遇 401 自动跳登录页；统一错误信息提取
import axios, { AxiosHeaders, type AxiosError, type InternalAxiosRequestConfig } from "axios";
import { toast } from "sonner";

const CSRF_COOKIE = "csrf_token";
const CSRF_HEADER = "X-CSRF-Token";
const REQUESTED_WITH_HEADER = "X-Requested-With";
const REQUESTED_WITH_VALUE = "telepilot-ui";
export const API_BASE = import.meta.env.VITE_API_BASE || "/";

let csrfFetch: Promise<string | null> | null = null;

type CsrfRetryConfig = InternalAxiosRequestConfig & {
  _csrfRetry?: boolean;
};

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  const item = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}

function needsCsrf(config: InternalAxiosRequestConfig) {
  const method = (config.method || "get").toUpperCase();
  return !["GET", "HEAD", "OPTIONS"].includes(method);
}

function writeRequestHeaders(config: InternalAxiosRequestConfig): AxiosHeaders {
  const headers = AxiosHeaders.from(config.headers);
  headers.set(REQUESTED_WITH_HEADER, REQUESTED_WITH_VALUE);
  config.headers = headers;
  return headers;
}

function clearCookie(name: string) {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=; Max-Age=0; path=/; SameSite=Lax`;
}

async function ensureCsrfToken(forceRefresh = false): Promise<string | null> {
  const existing = forceRefresh ? null : readCookie(CSRF_COOKIE);
  if (existing) return existing;
  if (!csrfFetch) {
    csrfFetch = api.get("/api/auth/csrf", { timeout: 5000 })
      .then(() => readCookie(CSRF_COOKIE))
      .finally(() => {
        csrfFetch = null;
      });
  }
  return csrfFetch;
}

export const api = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
  timeout: 15000,
  headers: {
    [REQUESTED_WITH_HEADER]: REQUESTED_WITH_VALUE,
  },
});

function resolveApiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const base = API_BASE.endsWith("/") ? API_BASE : `${API_BASE}/`;
  const relative = path.replace(/^\//, "");
  if (/^https?:\/\//i.test(base)) return new URL(relative, base).toString();
  return `${base}${relative}`.replace(/\/{2,}/g, "/");
}

/** Cloudflare Access 会话过期时的统一提示文案。 */
export const ACCESS_EXPIRED_MESSAGE = "Cloudflare Access 登录已过期，请刷新页面重新登录。";
const ACCESS_TOAST_ID = "cloudflare-access-expired";
let accessProbe: Promise<boolean> | null = null;

/**
 * 服务器前套了 Cloudflare Access 时，Access 会话过期后同源 API 请求会被 302 到
 * `<team>.cloudflareaccess.com`，浏览器跟随后因跨域失败，axios / fetch 只能看到“网络错误”。
 * 这里用一次不跟随重定向的只读 GET 探测：同源 API 返回 opaqueredirect 即视为 Access 拦截。
 * 探测结果短暂缓存，避免一批并发失败重复探测。
 */
export function detectAccessRedirect(): Promise<boolean> {
  if (typeof fetch !== "function") return Promise.resolve(false);
  if (!accessProbe) {
    accessProbe = fetch(resolveApiUrl("/api/auth/csrf"), {
      method: "GET",
      credentials: "include",
      redirect: "manual",
      cache: "no-store",
      headers: { [REQUESTED_WITH_HEADER]: REQUESTED_WITH_VALUE },
    })
      .then((response) => response.type === "opaqueredirect")
      .catch(() => false)
      .finally(() => {
        setTimeout(() => { accessProbe = null; }, 5000);
      });
  }
  return accessProbe;
}

/** 提示刷新页面重新完成 Access 登录（同一时间只显示一条）。不会自动重试任何写操作。 */
export function notifyAccessExpired() {
  toast.error(ACCESS_EXPIRED_MESSAGE, {
    id: ACCESS_TOAST_ID,
    duration: Infinity,
    action: { label: "刷新页面", onClick: () => location.reload() },
  });
}

/** 网络层失败（没有响应）时检查是否为 Access 会话过期；是则提示并返回 true。 */
async function checkAccessExpired(): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.onLine === false) return false;
  if (!(await detectAccessRedirect())) return false;
  notifyAccessExpired();
  return true;
}

/** fetch 版 API 通道，供浏览器 ReadableStream 等 Axios/XHR 无法覆盖的场景使用。 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set(REQUESTED_WITH_HEADER, REQUESTED_WITH_VALUE);
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const token = await ensureCsrfToken();
    if (token) headers.set(CSRF_HEADER, token);
  }

  const execute = () => fetch(resolveApiUrl(path), {
    ...init,
    method,
    headers,
    credentials: "include",
  });
  let response: Response;
  try {
    response = await execute();
  } catch (error) {
    if (!(init.signal?.aborted) && await checkAccessExpired()) throw new Error(ACCESS_EXPIRED_MESSAGE);
    throw error;
  }
  if (response.status === 403 && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    let code: string | undefined;
    try {
      const payload = await response.clone().json() as ApiErrorPayload;
      const detail = payload.detail;
      code = payload.error?.code
        || (!Array.isArray(detail) && typeof detail === "object" ? detail?.code : undefined);
    } catch {
      // 非 JSON 的 403 交给调用方按原响应处理。
    }
    if (code === "CSRF_TOKEN_REQUIRED" || code === "CSRF_HEADER_REQUIRED") {
      clearCookie(CSRF_COOKIE);
      const token = await ensureCsrfToken(true);
      if (token) headers.set(CSRF_HEADER, token);
      response = await execute();
    }
  }
  if (response.status === 401 && !location.pathname.startsWith("/login")) {
    location.href = "/login";
  }
  return response;
}

api.interceptors.request.use(async (config) => {
  const headers = writeRequestHeaders(config);
  if (needsCsrf(config)) {
    const token = await ensureCsrfToken();
    if (token) {
      headers.set(CSRF_HEADER, token);
    }
  }
  return config;
});

api.interceptors.response.use(
  (r) => r,
  async (err: AxiosError) => {
    const status = err.response?.status;
    const code = getErrCode(err);
    const config = err.config as CsrfRetryConfig | undefined;
    if (
      status === 403
      && config
      && needsCsrf(config)
      && !config._csrfRetry
      && (code === "CSRF_TOKEN_REQUIRED" || code === "CSRF_HEADER_REQUIRED")
    ) {
      config._csrfRetry = true;
      clearCookie(CSRF_COOKIE);
      const token = await ensureCsrfToken(true);
      const headers = writeRequestHeaders(config);
      if (token) {
        headers.set(CSRF_HEADER, token);
      }
      return api.request(config);
    }
    if (status === 401 && !location.pathname.startsWith("/login")) {
      location.href = "/login";
    }
    // 没有响应的网络错误：可能是 Cloudflare Access 会话过期导致的跨域重定向失败。
    if (!err.response && err.code === "ERR_NETWORK" && await checkAccessExpired()) {
      err.message = ACCESS_EXPIRED_MESSAGE;
    }
    return Promise.reject(err);
  },
);

// 后端错误统一形态：{ error: { code, message } }；FastAPI HTTPException 常见为 { detail: { code, message } }
type ApiErrorPayload = {
  error?: { code?: string; message?: string };
  detail?: { code?: string; message?: string } | string | Array<{ msg?: string; message?: string }>;
};

export function getErrMsg(err: unknown): string {
  const e = err as AxiosError<ApiErrorPayload>;
  const detail = e?.response?.data?.detail;
  const detailMessage = Array.isArray(detail)
    ? detail.map((item) => item.message || item.msg).filter(Boolean).join("；")
    : typeof detail === "object"
      ? detail?.message
      : undefined;
  const message = (
    e?.response?.data?.error?.message
    || detailMessage
    || (typeof detail === "string" ? detail : undefined)
    || e?.message
    || "请求失败"
  );
  if (message.includes("terminated by other getUpdates request") || message.includes("Conflict:")) {
    return "Bot polling 冲突：同一个 Bot token 正在被另一个实例监听。请确认它没有被其他账号、本地/Docker/VPS 中的另一套 TelePilot，或其他程序同时使用。";
  }
  return message;
}

export function getErrCode(err: unknown): string | undefined {
  const e = err as AxiosError<ApiErrorPayload>;
  const detail = e?.response?.data?.detail;
  return e?.response?.data?.error?.code || (!Array.isArray(detail) && typeof detail === "object" ? detail?.code : undefined);
}
