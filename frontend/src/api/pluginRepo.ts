import { api } from "@/lib/api";
import type { RemotePlugin } from "@/types/remotePlugin";
import type {
  InstallFromRepoBody,
  PluginRepo,
  PluginRepoCreate,
  PluginRepoPlugin,
} from "@/types/pluginRepo";

const BASE = "/api/plugin-repos";

export async function fetchPluginRepos(): Promise<PluginRepo[]> {
  const { data } = await api.get<PluginRepo[]>(BASE);
  return data;
}

export async function addPluginRepo(body: PluginRepoCreate): Promise<PluginRepo> {
  const { data } = await api.post<PluginRepo>(BASE, body);
  return data;
}

export async function deletePluginRepo(
  id: number,
): Promise<{ ok: boolean; id: number }> {
  const { data } = await api.delete(`${BASE}/${id}`);
  return data;
}

export async function fetchRepoPlugins(
  repoId: number,
): Promise<PluginRepoPlugin[]> {
  const { data } = await api.get<PluginRepoPlugin[]>(
    `${BASE}/${repoId}/plugins`,
  );
  return data;
}

export async function installFromRepo(
  repoId: number,
  pluginName: string,
  body?: InstallFromRepoBody,
): Promise<RemotePlugin> {
  const { data } = await api.post<RemotePlugin>(
    `${BASE}/${repoId}/plugins/${encodeURIComponent(pluginName)}/install`,
    body ?? {},
  );
  return data;
}

export async function fetchLocalPlugins(): Promise<PluginRepoPlugin[]> {
  const { data } = await api.get<PluginRepoPlugin[]>(
    `${BASE}/local/plugins`,
  );
  return data;
}

export async function installLocalPlugin(
  pluginName: string,
  body?: InstallFromRepoBody,
): Promise<RemotePlugin> {
  const { data } = await api.post<RemotePlugin>(
    `${BASE}/local/plugins/${encodeURIComponent(pluginName)}/install`,
    body ?? {},
  );
  return data;
}
