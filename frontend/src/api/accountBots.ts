import { api } from "@/lib/api";
import type {
  AccountBotConfig,
  AccountBotConfigUpdate,
  AccountBotInteractionConfig,
  AccountBotInteractionConfigUpdate,
  AccountBotTestRequest,
  AccountBotTestResponse,
  AccountBotUser,
  AccountBotUserCreate,
  AccountBotUserUpdate,
} from "@/api/types";

export async function getAccountBot(aid: number): Promise<AccountBotConfig> {
  const { data } = await api.get<AccountBotConfig>(`/api/accounts/${aid}/bot`);
  return data;
}

export async function updateAccountBot(
  aid: number,
  payload: AccountBotConfigUpdate,
): Promise<AccountBotConfig> {
  const { data } = await api.put<AccountBotConfig>(`/api/accounts/${aid}/bot`, payload);
  return data;
}

export async function testAccountBot(
  aid: number,
  payload?: AccountBotTestRequest,
): Promise<AccountBotTestResponse> {
  const { data } = await api.post<AccountBotTestResponse>(
    `/api/accounts/${aid}/bot/test`,
    payload ?? {},
  );
  return data;
}

export async function restartAccountBotRuntime(aid: number): Promise<void> {
  await api.post(`/api/accounts/${aid}/bot/restart-runtime`);
}

export async function getInteractionBotConfig(
  aid: number,
): Promise<AccountBotInteractionConfig> {
  const { data } = await api.get<AccountBotInteractionConfig>(
    `/api/accounts/${aid}/interaction-bot`,
  );
  return data;
}

export async function updateInteractionBotConfig(
  aid: number,
  payload: AccountBotInteractionConfigUpdate,
): Promise<AccountBotInteractionConfig> {
  const { data } = await api.put<AccountBotInteractionConfig>(
    `/api/accounts/${aid}/interaction-bot`,
    payload,
  );
  return data;
}

export const getTransferNoticeConfig = getInteractionBotConfig;
export const updateTransferNoticeConfig = updateInteractionBotConfig;

export async function listAccountBotUsers(aid: number): Promise<AccountBotUser[]> {
  const { data } = await api.get<AccountBotUser[]>(`/api/accounts/${aid}/bot/users`);
  return data;
}

export async function createAccountBotUser(
  aid: number,
  payload: AccountBotUserCreate,
): Promise<AccountBotUser> {
  const { data } = await api.post<AccountBotUser>(
    `/api/accounts/${aid}/bot/users`,
    payload,
  );
  return data;
}

export async function updateAccountBotUser(
  aid: number,
  uid: number,
  payload: AccountBotUserUpdate,
): Promise<AccountBotUser> {
  const { data } = await api.patch<AccountBotUser>(
    `/api/accounts/${aid}/bot/users/${uid}`,
    payload,
  );
  return data;
}

export async function deleteAccountBotUser(aid: number, uid: number): Promise<void> {
  await api.delete(`/api/accounts/${aid}/bot/users/${uid}`);
}
