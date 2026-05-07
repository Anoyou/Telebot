import { api } from "@/lib/api";
import type { SudoUserCreate, SudoUserResponse, SudoUserUpdate } from "@/types/sudo";

export async function getSudoUsers(accountId?: number): Promise<SudoUserResponse[]> {
  const params = accountId ? { account_id: accountId } : {};
  const res = await api.get("/api/sudo", { params });
  return res.data;
}

export async function getSudoUser(id: number): Promise<SudoUserResponse> {
  const res = await api.get(`/api/sudo/${id}`);
  return res.data;
}

export async function createSudoUser(
  data: SudoUserCreate
): Promise<SudoUserResponse> {
  const res = await api.post("/api/sudo", data);
  return res.data;
}

export async function updateSudoUser(
  id: number,
  data: SudoUserUpdate
): Promise<SudoUserResponse> {
  const res = await api.patch(`/api/sudo/${id}`, data);
  return res.data;
}

export async function deleteSudoUser(id: number): Promise<void> {
  await api.delete(`/api/sudo/${id}`);
}
