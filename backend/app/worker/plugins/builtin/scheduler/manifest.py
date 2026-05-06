"""scheduler 插件 manifest。"""

from __future__ import annotations

from app.db.models.feature import FEATURE_SCHEDULER
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key=FEATURE_SCHEDULER,
    display_name="定时任务",
    version="0.2.0",
    author="builtin",
    description="cron / once / interval 定时触发动作（send_message / run_command / call_llm）",
    permissions=["send_message", "send_file"],
)

__all__ = ["MANIFEST"]
