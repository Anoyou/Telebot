// axios 客户端：携带 cookie；遇 401 自动跳登录页；统一错误信息提取
import axios, { AxiosHeaders, type AxiosError, type InternalAxiosRequestConfig } from "axios";

const CSRF_COOKIE = "csrf_token";
const CSRF_HEADER = "X-CSRF-Token";

let csrfFetch: Promise<string | null> | null = null;

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

async function ensureCsrfToken(): Promise<string | null> {
  const existing = readCookie(CSRF_COOKIE);
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
  baseURL: import.meta.env.VITE_API_BASE || "/",
  withCredentials: true,
  timeout: 15000,
  headers: {
    "X-Requested-With": "telepilot-ui",
  },
});

api.interceptors.request.use(async (config) => {
  if (needsCsrf(config)) {
    const token = await ensureCsrfToken();
    if (token) {
      config.headers = AxiosHeaders.from(config.headers);
      config.headers.set(CSRF_HEADER, token);
    }
  }
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err: AxiosError) => {
    const status = err.response?.status;
    if (status === 401 && !location.pathname.startsWith("/login")) {
      location.href = "/login";
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
