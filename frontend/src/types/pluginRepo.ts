export interface PluginRepo {
  id: number;
  name: string;
  url: string;
  description: string;
  auth_type: "none" | "github_token" | string;
  has_credentials: boolean;
  added_at: string | null;
  updated_at: string | null;
}

export interface PluginRepoCredentialUpdate {
  auth_type?: "none" | "github_token" | string | null;
  token?: string | null;
}

export interface PluginRepoCreate {
  url: string;
  name?: string | null;
  description?: string | null;
  credential?: PluginRepoCredentialUpdate | null;
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
  tags?: string[];
  subdir: string;
}

export interface InstallFromRepoBody {
  default_enabled?: boolean;
}
