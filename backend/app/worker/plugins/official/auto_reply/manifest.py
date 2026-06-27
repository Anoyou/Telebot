"""auto_reply 插件 manifest。

Config Schema 说明：
- level: "global" 的字段为全局配置，所有账号共享
- 无 level 或 level: "account" 的字段为账号级配置
- 配置合并顺序：schema defaults < global config < account config
"""

from __future__ import annotations

from app.db.models.feature import FEATURE_AUTO_REPLY
from app.worker.plugins.manifest import Manifest

# 顶层导出常量；loader 扫描时读取
MANIFEST = Manifest(
    key=FEATURE_AUTO_REPLY,
    display_name="自动回复",
    version="0.1.0",
    author="TelePilot Official",
    description="按规则匹配关键词或正则后自动回复目标会话",
    category="automation",
    permissions=["send_message", "edit_message", "read_chat"],
    # 该插件目前无 config_schema，账号级配置通过 rules 表管理
)

__all__ = ["MANIFEST"]
