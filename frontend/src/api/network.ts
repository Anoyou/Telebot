// 网络环境探测：当前后端进程出口 IP / 国家 / 地区
import { api } from "@/lib/api";
import type { NetworkInfo } from "@/api/types";

export async function getNetworkInfo(): Promise<NetworkInfo> {
  const { data } = await api.get<NetworkInfo>("/api/system/network");
  return data;
}

export async function refreshNetworkInfo(): Promise<NetworkInfo> {
  const { data } = await api.post<NetworkInfo>("/api/system/network/refresh");
  return data;
}
