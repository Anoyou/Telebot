// 鉴权相关的 API 包装
import { api } from "./api";
import type { CurrentUser, LoginRequest, LoginResponse } from "@/api/types";

export async function fetchMe(): Promise<CurrentUser> {
  const { data } = await api.get<CurrentUser>("/api/auth/me");
  return data;
}

export async function login(payload: LoginRequest): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/api/auth/login", payload);
  return data;
}

export async function logout(): Promise<void> {
  await api.post("/api/auth/logout");
}

export async function register(
  username: string,
  password: string,
): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/api/auth/register", {
    username,
    password,
  });
  return data;
}
