export interface SudoUserCreate {
  account_id: number;
  tg_user_id: number;
  display_name?: string;
  allowed_chat_ids?: number[];
  allowed_commands?: string[];
}

export interface SudoUserUpdate {
  display_name?: string;
  allowed_chat_ids?: number[];
  allowed_commands?: string[];
}

export interface SudoUserResponse {
  id: number;
  account_id: number;
  tg_user_id: number;
  display_name?: string;
  allowed_chat_ids?: number[];
  allowed_commands?: string[];
  created_at: string;
}
