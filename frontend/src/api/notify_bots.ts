import { api } from "@/lib/api";
import type {
  NotifyBotCreate,
  NotifyBotOut,
  NotifyBotTestRequest,
  NotifyBotUpdate,
} from "@/api/types";

export async function listNotifyBots(): Promise<NotifyBotOut[]> {
  const { data } = await api.get<NotifyBotOut[]>("/api/notify-bots");
  return data;
}

export async function createNotifyBot(payload: NotifyBotCreate): Promise<NotifyBotOut> {
  const { data } = await api.post<NotifyBotOut>("/api/notify-bots", payload);
  return data;
}

export async function updateNotifyBot(
  id: number,
  payload: NotifyBotUpdate,
): Promise<NotifyBotOut> {
  const { data } = await api.patch<NotifyBotOut>(`/api/notify-bots/${id}`, payload);
  return data;
}

export async function deleteNotifyBot(id: number): Promise<void> {
  await api.delete(`/api/notify-bots/${id}`);
}

export async function testNotifyBot(
  id: number,
  payload: NotifyBotTestRequest = {},
): Promise<{ ok: boolean }> {
  const { data } = await api.post<{ ok: boolean }>(`/api/notify-bots/${id}/test`, payload);
  return data;
}
