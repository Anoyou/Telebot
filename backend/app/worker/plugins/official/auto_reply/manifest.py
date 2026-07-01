"""auto_reply 插件 manifest。

Config Schema 说明：
- level: "global" 的字段为全局配置，所有账号共享
- 无 level 或 level: "account" 的字段为账号级配置
- 配置合并顺序：schema defaults < global config < account config
"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

FEATURE_AUTO_REPLY = "auto_reply"

# 顶层导出常量；loader 扫描时读取
MANIFEST = Manifest(
    key=FEATURE_AUTO_REPLY,
    display_name="自动回复",
    version="0.1.0",
    author="TelePilot Official",
    description="按规则匹配关键词或正则后自动回复目标会话",
    usage="自动回复通过规则触发：每条规则配置匹配范围、关键词或正则、回复模板和冷却限制。安装后到插件中心选择账号，再进入自动回复配置页新增规则。规则保存后会随账号 worker 热更新生效。",
    category="automation",
    permissions=["send_message", "edit_message", "read_chat"],
    event_subscriptions=[
        {
            "source": ["userbot"],
            "events": ["message"],
            "scope": "rule_bound",
            "entry_key": "rules",
        }
    ],
    capabilities={},
    # 该插件目前无 config_schema，账号级配置通过 rules 表管理
)

__all__ = ["MANIFEST"]
