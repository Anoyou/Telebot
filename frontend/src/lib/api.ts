// axios 客户端：携带 cookie；遇 401 自动跳登录页；统一错误信息提取
import axios, { type AxiosError } from "axios";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || "/",
  withCredentials: true,
  timeout: 15000,
  headers: {
    "X-Requested-With": "telebot-ui",
  },
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
  return (
    e?.response?.data?.error?.message
    || detailMessage
    || (typeof detail === "string" ? detail : undefined)
    || e?.message
    || "请求失败"
  );
}

export function getErrCode(err: unknown): string | undefined {
  const e = err as AxiosError<ApiErrorPayload>;
  const detail = e?.response?.data?.detail;
  return e?.response?.data?.error?.code || (!Array.isArray(detail) && typeof detail === "object" ? detail?.code : undefined);
}
