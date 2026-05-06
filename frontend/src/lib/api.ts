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

// 后端错误统一形态：{ error: { code, message } }
type ApiErrorPayload = { error?: { code?: string; message?: string } };

export function getErrMsg(err: unknown): string {
  const e = err as AxiosError<ApiErrorPayload>;
  return e?.response?.data?.error?.message || e?.message || "请求失败";
}

export function getErrCode(err: unknown): string | undefined {
  const e = err as AxiosError<ApiErrorPayload>;
  return e?.response?.data?.error?.code;
}
