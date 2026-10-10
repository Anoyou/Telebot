"""从上游 ``/v1/models`` 响应里提取模型元数据。

覆盖三类上游的字段命名，同一字段名冲突时按上游专有字段优先：

- Magpie 网关（``/v1/models``）：``display_name`` / ``owned_by`` /
  ``context_window``＝``context_length``＝``max_input_tokens`` / ``max_output_tokens`` /
  ``supported_reasoning_levels``（``[{"effort": "low"}]``）/ ``modalities.input`` /
  ``native_endpoints`` / ``capabilities``。
- OpenRouter：``name`` / ``context_length`` / ``top_provider.max_completion_tokens`` /
  ``architecture.input_modalities``。
- LiteLLM 代理：``max_input_tokens`` / ``max_output_tokens`` / ``input_cost_per_token``。

本模块只负责"读"，不做任何持久化；合并策略见 :func:`merge_model_entry`——
上游元数据只填空缺，绝不覆盖用户已经勾选的 ``enabled``、``custom`` 和手填字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# TelePilot 支持的推理档位；上游可能给出 "none" / "min" 等别名。
REASONING_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")

_REASONING_ALIASES = {
    "min": "minimal",
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "mid": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "extra_high": "xhigh",
    "max": "max",
    "maximum": "max",
}

_MODALITY_ALIASES = {
    "text": "text",
    "image": "image",
    "images": "image",
    "vision": "image",
    "audio": "audio",
    "video": "video",
}

# Magpie ``native_endpoints`` / Cursor ``api_types`` → TelePilot api_format。
_ENDPOINT_FORMATS = {
    "/v1/chat/completions": "chat_completions",
    "chat_completions": "chat_completions",
    "chat": "chat_completions",
    "/v1/responses": "responses",
    "responses": "responses",
    "/v1/messages": "anthropic_messages",
    "anthropic_messages": "anthropic_messages",
    "messages": "anthropic_messages",
}


@dataclass
class ModelMetadata:
    """一条上游模型条目的可用元数据；缺失项一律 ``None``。"""

    label: str | None = None
    owned_by: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    supported_reasoning_levels: list[str] | None = None
    default_reasoning_level: str | None = None
    supported_api_formats: list[str] | None = None
    supports_vision: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return self.to_fields() == {}

    def to_fields(self) -> dict[str, Any]:
        """转成 :class:`~app.schemas.command.ProviderModel` 可用的字段字典。"""

        out: dict[str, Any] = {}
        if self.label:
            out["label"] = self.label
        if self.context_window:
            out["context_window"] = int(self.context_window)
        if self.max_output_tokens:
            out["max_output_tokens"] = int(self.max_output_tokens)
        if self.input_modalities:
            out["input_modalities"] = list(self.input_modalities)
            out["supports_images"] = "image" in self.input_modalities
        if self.output_modalities:
            out["output_modalities"] = [
                m for m in self.output_modalities if m in {"text", "image", "audio"}
            ] or None
        if self.supported_reasoning_levels:
            out["supported_reasoning_levels"] = list(self.supported_reasoning_levels)
            out["reasoning_efforts"] = list(self.supported_reasoning_levels)
        if self.default_reasoning_level:
            out["default_reasoning_level"] = self.default_reasoning_level
        if self.supported_api_formats:
            out["supported_api_formats"] = list(self.supported_api_formats)
        return {k: v for k, v in out.items() if v is not None}


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number if number > 0 else None
    if isinstance(value, str):
        try:
            number = int(float(value.strip()))
        except ValueError:
            return None
        return number if number > 0 else None
    return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        number = _int_or_none(value)
        if number is not None:
            return number
    return None


def _clean_text(value: Any, *, limit: int = 128) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] if text else None


def _modalities(values: Any) -> list[str] | None:
    items: list[Any] = []
    if isinstance(values, str):
        # models.dev 风格形如 "text+image->text"。
        head = values.split("->", 1)[0]
        items = [part for part in head.replace("+", ",").split(",") if part]
    elif isinstance(values, list):
        items = values
    out: list[str] = []
    for item in items:
        key = str(item).strip().lower()
        mapped = _MODALITY_ALIASES.get(key)
        if mapped and mapped not in out:
            out.append(mapped)
    return out or None


def _reasoning_levels(values: Any) -> list[str] | None:
    """归一化推理档位列表；接受 ``["low"]`` 与 ``[{"effort": "low"}]`` 两种形态。"""

    items: list[Any] = []
    if isinstance(values, dict):
        items = list(values.values())
    elif isinstance(values, list):
        items = values
    elif isinstance(values, str):
        items = [values]
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            raw = item.get("effort") or item.get("level") or item.get("value")
        else:
            raw = item
        mapped = _REASONING_ALIASES.get(str(raw or "").strip().lower())
        if mapped and mapped not in out:
            out.append(mapped)
    if not out:
        return None
    # 按 TelePilot 档位顺序输出，便于前端稳定展示。
    return [level for level in REASONING_LEVELS if level in out]


def _api_formats(values: Any) -> list[str] | None:
    items = values if isinstance(values, list) else [values]
    out: list[str] = []
    for item in items:
        mapped = _ENDPOINT_FORMATS.get(str(item or "").strip().lower())
        if mapped and mapped not in out:
            out.append(mapped)
    return out or None


def is_chat_model(entry: Any) -> bool:
    """过滤掉 Magpie 的绘图模型（``kind: image``）等非对话条目。"""

    if not isinstance(entry, dict):
        return False
    if str(entry.get("kind") or "").strip().lower() in {"image", "draw", "embedding"}:
        return False
    if str(entry.get("type") or "").strip().lower() in {"image", "embedding"}:
        return False
    return True


def parse_model_entry(entry: Any) -> ModelMetadata:
    """解析单条 ``/v1/models`` 条目；无法识别的字段直接忽略。"""

    if not isinstance(entry, dict):
        return ModelMetadata()
    capabilities = _as_mapping(entry.get("capabilities"))
    architecture = _as_mapping(entry.get("architecture"))
    modalities = _as_mapping(entry.get("modalities"))
    top_provider = _as_mapping(entry.get("top_provider"))
    reasoning = _as_mapping(entry.get("reasoning"))
    reasoning_options = _as_mapping(entry.get("reasoning_options"))

    model_id = _clean_text(entry.get("id"))
    label = _clean_text(entry.get("display_name") or entry.get("name") or entry.get("magpie_label"))
    # label 与 id 相同（或仅去掉 provider 前缀后相同）时不落库，避免表格里出现重复列。
    if label and model_id:
        if label == model_id or model_id.endswith(f"/{label}"):
            label = None

    input_modalities = (
        _modalities(modalities.get("input"))
        or _modalities(capabilities.get("input_modalities"))
        or _modalities(architecture.get("input_modalities"))
        or _modalities(architecture.get("modality"))
    )
    vision = capabilities.get("supports_vision")
    supports_vision = vision if isinstance(vision, bool) else None
    if input_modalities is None and supports_vision:
        input_modalities = ["text", "image"]

    levels = _reasoning_levels(
        entry.get("supported_reasoning_levels")
        or capabilities.get("reasoning_effort")
        or entry.get("reasoning_efforts")
        or entry.get("supported_reasoning_efforts")
        or reasoning_options.get("values")
        or reasoning.get("efforts")
    )

    default_level = _reasoning_levels(
        [
            entry.get("default_reasoning_level")
            or entry.get("default_reasoning_effort")
            or reasoning.get("default")
            or reasoning.get("default_level")
        ]
    )
    default_level = default_level[0] if default_level else None
    if default_level and levels and default_level not in levels:
        default_level = None

    return ModelMetadata(
        label=label,
        owned_by=_clean_text(entry.get("owned_by")),
        context_window=_first_int(
            entry.get("context_window"),
            entry.get("context_length"),
            entry.get("max_input_tokens"),
            entry.get("max_model_len"),
            capabilities.get("context_length"),
            top_provider.get("context_length"),
        ),
        max_output_tokens=_first_int(
            entry.get("max_output_tokens"),
            entry.get("max_completion_tokens"),
            capabilities.get("max_output_tokens"),
            top_provider.get("max_completion_tokens"),
        ),
        input_modalities=input_modalities,
        output_modalities=(
            _modalities(modalities.get("output"))
            or _modalities(capabilities.get("output_modalities"))
            or _modalities(architecture.get("output_modalities"))
        ),
        supported_reasoning_levels=levels,
        default_reasoning_level=default_level,
        supported_api_formats=(
            _api_formats(entry.get("native_endpoints")) or _api_formats(entry.get("api_types"))
        ),
        supports_vision=supports_vision,
    )


def parse_models_payload(data: Any) -> dict[str, ModelMetadata]:
    """解析整个响应体，返回 ``{model_id: ModelMetadata}``（保持上游顺序）。"""

    items: Any = None
    if isinstance(data, dict):
        items = data.get("data")
        if not isinstance(items, list):
            items = data.get("models")
    elif isinstance(data, list):
        items = data
    if not isinstance(items, list):
        return {}

    out: dict[str, ModelMetadata] = {}
    for entry in items:
        if isinstance(entry, str):
            continue
        if not is_chat_model(entry):
            continue
        model_id = _clean_text(_as_mapping(entry).get("id"))
        if not model_id or model_id in out:
            continue
        out[model_id] = parse_model_entry(entry)
    return out


def merge_model_entry(existing: dict[str, Any] | None, meta: ModelMetadata) -> dict[str, Any]:
    """把上游元数据合并进模型清单条目：只填空缺，不覆盖用户设置。

    用户可手改的字段（``enabled`` / ``custom`` / 手填 ``label`` 与能力标记）一律保留；
    上游只负责补齐 ``context_window`` / ``max_output_tokens`` / 模态 / 推理档位等
    用户没有理由手填、但缺失就会影响路由与表单默认值的字段。
    """

    row: dict[str, Any] = dict(existing or {})
    if not row.get("label") and meta.label:
        row["label"] = meta.label
    if not row.get("metadata_source"):
        row["metadata_source"] = "upstream"
    for key, value in meta.to_fields().items():
        if key == "label":
            continue
        if row.get(key) in (None, [], {}):
            row[key] = value
    return row
