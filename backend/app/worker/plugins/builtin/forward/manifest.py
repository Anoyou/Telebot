"""forward 插件 manifest。"""

from __future__ import annotations

from app.db.models.feature import FEATURE_FORWARD
from app.worker.plugins.manifest import Manifest

# 顶层导出常量；loader 扫描时读取
MANIFEST = Manifest(
    key=FEATURE_FORWARD,
    display_name="消息转发",
    version="0.2.0",
    author="builtin",
    description="按规则把 incoming 消息转发到指定 chat（4 种 mode + 风控接入 + FloodWait 兜底）",
    permissions=["read_chat", "send_message", "send_file"],
    # rule.config 的 JSON Schema —— 前端可据此渲染表单 / 做兜底校验
    config_schema={
        "type": "object",
        "required": ["target_chat_id", "mode"],
        "properties": {
            # 源筛选：all = 任何 incoming；peers = 指定 chat_id 列表；keyword = 文本包含关键词
            "source_kind": {"enum": ["all", "peers", "keyword"]},
            "source_peers": {"type": "array", "items": {"type": "integer"}},
            "keyword": {"type": "string"},
            # 目标 chat_id（Telethon 形式：私聊正数 / 普通群 -xxx / 超级群与频道 -100xxx）
            "target_chat_id": {"type": "integer"},
            # 4 种转发方式
            "mode": {
                "enum": ["forward_native", "copy_text", "quote", "link_only"],
            },
            # include_media=False 时仅转纯文本，遇到含媒体消息直接跳过
            "include_media": {"type": "boolean"},
            # copy / quote 模式下可选的固定前缀
            "header": {"type": "string"},
        },
    },
)

__all__ = ["MANIFEST"]
