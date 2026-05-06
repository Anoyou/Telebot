// 忽略 peer API：列表 / 加入 / 移除 + 最近活跃会话
import { api } from "@/lib/api";
import type {
  IgnoredPeer,
  IgnoredPeerCreate,
  RecentPeersResponse,
} from "@/api/types";

/** 列出账号已忽略的 peer */
export async function listIgnoredPeers(aid: number): Promise<IgnoredPeer[]> {
  const { data } = await api.get<IgnoredPeer[]>(
    `/api/accounts/${aid}/ignored-peers`,
  );
  return data;
}

/** 加入忽略名单（幂等：同 peer_id 已存在则后端返回原行） */
export async function addIgnoredPeer(
  aid: number,
  payload: IgnoredPeerCreate,
): Promise<IgnoredPeer> {
  const { data } = await api.post<IgnoredPeer>(
    `/api/accounts/${aid}/ignored-peers`,
    payload,
  );
  return data;
}

/** 从忽略名单移除一行 */
export async function removeIgnoredPeer(
  aid: number,
  ignoredId: number,
): Promise<void> {
  await api.delete(`/api/accounts/${aid}/ignored-peers/${ignoredId}`);
}

/**
 * 拉 worker 内存中的最近活跃 peer 列表（≤50 条）+ worker 是否在跑。
 *
 * 后端把"worker 离线"和"worker 在跑只是没收到消息"分开报告，
 * 前端据此给出精准引导，而不是一律提示"暂无活跃会话"。
 */
export async function listRecentPeers(
  aid: number,
): Promise<RecentPeersResponse> {
  const { data } = await api.get<RecentPeersResponse>(
    `/api/accounts/${aid}/recent-peers`,
  );
  return data;
}
