export interface CommandAliasCreate {
  alias: string;
  target: string;
  account_id?: number;
}

export interface CommandAliasUpdate {
  target: string;
  account_id?: number;
}

export interface CommandAliasResponse {
  id: number;
  alias: string;
  target: string;
  account_id?: number;
  created_at: string;
}
