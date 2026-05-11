export interface PluginRepo {
  id: number;
  name: string;
  url: string;
  description: string;
  added_at: string | null;
  updated_at: string | null;
}

export interface PluginRepoCreate {
  url: string;
  name?: string | null;
  description?: string | null;
}

export interface PluginRepoPlugin {
  name: string;
  display_name: string;
  description: string;
  author: string;
  version: string;
  installed: boolean;
  installed_version?: string | null;
  update_available?: boolean;
  subdir: string;
}

export interface InstallFromRepoBody {
  default_enabled?: boolean;
}
