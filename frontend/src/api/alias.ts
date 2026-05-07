import { api } from "@/lib/api";
import type { CommandAliasCreate, CommandAliasResponse, CommandAliasUpdate } from "@/types/alias";

export async function getAliases(accountId?: number): Promise<CommandAliasResponse[]> {
  const params = accountId ? { account_id: accountId } : {};
  const res = await api.get("/api/aliases", { params });
  return res.data;
}

export async function getAlias(id: number): Promise<CommandAliasResponse> {
  const res = await api.get(`/api/aliases/${id}`);
  return res.data;
}

export async function createAlias(
  data: CommandAliasCreate
): Promise<CommandAliasResponse> {
  const res = await api.post("/api/aliases", data);
  return res.data;
}

export async function updateAlias(
  id: number,
  data: CommandAliasUpdate
): Promise<CommandAliasResponse> {
  const res = await api.patch(`/api/aliases/${id}`, data);
  return res.data;
}

export async function deleteAlias(id: number): Promise<void> {
  await api.delete(`/api/aliases/${id}`);
}
