"""ui_page_demo 示例 manifest（ui 声明以 plugin.json 为准，这里保持一致）。"""

from __future__ import annotations

import json
from pathlib import Path

from app.worker.plugins.manifest import Manifest

_META = json.loads((Path(__file__).with_name("plugin.json")).read_text(encoding="utf-8"))
EVENT_SUBSCRIPTIONS = _META["event_subscriptions"]

MANIFEST = Manifest(
    key="ui_page_demo",
    display_name=_META["display_name"],
    version=_META["version"],
    min_telepilot_version=_META["min_telepilot_version"],
    author="examples",
    description=_META["description"],
    usage=_META["usage"],
    category="utility",
    permissions=[],
    requires_platform_capabilities=[],
    event_subscriptions=EVENT_SUBSCRIPTIONS,
    config_schema=_META["config_schema"],
    capabilities={},
    interaction_profile="utility_trigger",
)

__all__ = ["MANIFEST", "EVENT_SUBSCRIPTIONS"]
