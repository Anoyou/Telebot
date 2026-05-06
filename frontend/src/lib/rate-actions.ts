// 风控动作（rate-limit action）的中文标签 + 一句话说明
//
// 18 个 key 与后端 services/rate_limit_service.py 的 `_DEFAULTS` 字典一一对齐。
// 改 key 名字之前先去后端确认；不要在这里发明后端不存在的 action。
//
// 用法：
//   import { actionLabel, actionHint } from "@/lib/rate-actions";
//   <span title={actionHint(r.action)}>{actionLabel(r.action)}</span>

export interface ActionInfo {
  /** 表格里显示的中文短标签（≤ 8 字） */
  label: string;
  /** 鼠标悬停 / 帮助行里的一句话说明（≤ 30 字） */
  hint: string;
}

export const ACTION_INFO: Record<string, ActionInfo> = {
  // ── 发消息 ────────────────────────────────
  send_message_private: {
    label: "私聊发消息",
    hint: "向单个用户/Bot 发送一条消息",
  },
  send_message_group: {
    label: "群里发消息",
    hint: "在群组或超级群中发送一条消息",
  },
  same_peer_send: {
    label: "同会话连发",
    hint: "短时间向同一个会话连续发送消息的频率上限",
  },

  // ── 编辑/删除 ─────────────────────────────
  edit_message: {
    label: "编辑消息",
    hint: "修改自己已发出的消息内容",
  },
  delete_message: {
    label: "删除消息",
    hint: "撤回/删除一条消息",
  },

  // ── 转发 ──────────────────────────────────
  forward_message: {
    label: "转发消息",
    hint: "把别处的消息原样转发到指定会话",
  },

  // ── 交互 ──────────────────────────────────
  callback_query: {
    label: "按钮回调",
    hint: "点击 inline keyboard 按钮触发的回调",
  },
  read_history: {
    label: "标记已读",
    hint: "把会话的最新消息标记为已读",
  },

  // ── 入/退/建群 ────────────────────────────
  join_chat: {
    label: "加入群组",
    hint: "加入公开群/超级群/频道",
  },
  leave_chat: {
    label: "退出群组",
    hint: "离开当前所在群组/频道",
  },
  create_chat: {
    label: "建群",
    hint: "创建新的群或频道",
  },

  // ── 邀请/陌生人 ───────────────────────────
  invite_user: {
    label: "邀请用户",
    hint: "把用户拉进群（被动添加，最敏感的反垃圾动作之一）",
  },
  dm_stranger: {
    label: "私聊陌生人",
    hint: "向没有共同群的用户主动开私聊（极易触发 PeerFlood）",
  },

  // ── 资料 ──────────────────────────────────
  update_profile: {
    label: "修改资料",
    hint: "改昵称/简介/头像/用户名等个人资料",
  },

  // ── 文件 ──────────────────────────────────
  upload_file: {
    label: "上传文件",
    hint: "发送图片/视频/文件等媒体（按上传次数计费，不分大小）",
  },
  download_file: {
    label: "下载文件",
    hint: "拉取媒体到本地（受限频率较宽松）",
  },

  // ── 搜索 ──────────────────────────────────
  search: {
    label: "搜索",
    hint: "全局或群内消息搜索",
  },

  // ── 全局 ──────────────────────────────────
  api_total: {
    label: "API 总量",
    hint: "本账号所有 API 调用的总速率上限（绕过单个动作的天花板）",
  },
};

/** 取人类可读标签；未知 action 直接返回原 key */
export function actionLabel(action: string): string {
  return ACTION_INFO[action]?.label ?? action;
}

/** 取一句话说明；未知 action 返回空串 */
export function actionHint(action: string): string {
  return ACTION_INFO[action]?.hint ?? "";
}
