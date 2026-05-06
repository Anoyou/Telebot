"""auto_reply 插件 manifest。"""

from __future__ import annotations

from app.db.models.feature import FEATURE_AUTO_REPLY
from app.worker.plugins.manifest import Manifest

# 顶层导出常量；loader 扫描时读取
MANIFEST = Manifest(
    key=FEATURE_AUTO_REPLY,
    display_name="自动回复",
    version="0.1.0",
    author="builtin",
    description="按规则匹配关键词或正则后自动回复目标会话",
    permissions=["send_message", "edit_message", "read_chat"],
)

__all__ = ["MANIFEST"]
