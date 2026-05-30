import { api } from "@/lib/api";

export type MessageTemplateMode = "html" | "markdown" | "plain";

export interface MessageTemplateVariableDescriptor {
  key?: string;
  name?: string;
  label?: string;
  title?: string;
  description?: string;
  required?: boolean;
  default?: unknown;
  example?: unknown;
  value?: unknown;
}

export interface MessageTemplateCatalogItem {
  id?: string;
  key?: string;
  name?: string;
  template_key?: string;
  title?: string;
  label?: string;
  display_name?: string;
  description?: string | null;
  source?: string;
  source_key?: string;
  source_title?: string;
  group?: string;
  group_key?: string;
  group_title?: string;
  feature_key?: string | null;
  field_key?: string;
  format?: MessageTemplateMode | string;
  parse_mode?: string | null;
  content?: string;
  template?: string;
  text?: string;
  html?: string;
  body?: string;
  variables?: Record<string, unknown> | MessageTemplateVariableDescriptor[];
  sample_variables?: Record<string, unknown>;
  sample_data?: Record<string, unknown>;
  example_variables?: Record<string, unknown>;
  defaults?: Record<string, unknown>;
  meta?: Record<string, unknown> | null;
}

export interface MessageTemplateCatalogGroup {
  id?: string;
  key?: string;
  name?: string;
  title?: string;
  label?: string;
  source?: string;
  source_key?: string;
  source_title?: string;
  templates?: MessageTemplateCatalogItem[];
  items?: MessageTemplateCatalogItem[];
}

export interface MessageTemplateCatalogSource {
  id?: string;
  key?: string;
  name?: string;
  title?: string;
  label?: string;
  type?: string;
  groups?: MessageTemplateCatalogGroup[];
  templates?: MessageTemplateCatalogItem[];
}

export interface MessageTemplateCatalogResponse {
  account_id?: number;
  sources?: MessageTemplateCatalogSource[];
  groups?: MessageTemplateCatalogGroup[];
  templates?: MessageTemplateCatalogItem[];
  items?: MessageTemplateCatalogItem[];
}

export interface MessageTemplateEntity {
  type: string;
  raw_type?: string;
  offset?: number;
  length?: number;
  text?: string;
  url?: string;
  language?: string;
  custom_emoji_id?: string;
  collapsed?: boolean | null;
}

export interface MessageTemplateRenderRequest {
  template: string;
  sample_data?: Record<string, unknown>;
  parse_mode?: string | null;
}

export interface MessageTemplateRenderResponse {
  ok?: boolean;
  text?: string;
  rendered_text?: string;
  html?: string;
  parse_mode?: string | null;
  plain_text?: string;
  entities?: MessageTemplateEntity[];
  entity_summary?: MessageTemplateEntity[];
  validation?: {
    ok: boolean;
    errors?: string[];
    warnings?: string[];
    plain_text?: string;
  };
  warnings?: string[];
  message?: string;
}

export interface MessageTemplateTestSendRequest {
  account_id: number;
  target_chat_id: number;
  text: string;
  parse_mode?: string | null;
}

export interface MessageTemplateTestSendResponse {
  ok?: boolean;
  sent?: number;
  message?: string;
  target_chat_id?: number;
  parse_mode?: string | null;
  message_id?: number | null;
}

export async function getMessageTemplateCatalog(
  accountId: number,
): Promise<MessageTemplateCatalogResponse> {
  const { data } = await api.get<MessageTemplateCatalogResponse>(
    "/api/message-templates/catalog",
    { params: { account_id: accountId } },
  );
  return data;
}

export async function renderMessageTemplate(
  payload: MessageTemplateRenderRequest,
): Promise<MessageTemplateRenderResponse> {
  const { data } = await api.post<MessageTemplateRenderResponse>(
    "/api/message-templates/render",
    payload,
  );
  return data;
}

export async function testSendMessageTemplate(
  payload: MessageTemplateTestSendRequest,
): Promise<MessageTemplateTestSendResponse> {
  const { data } = await api.post<MessageTemplateTestSendResponse>(
    "/api/message-templates/test-send",
    payload,
  );
  return data;
}
