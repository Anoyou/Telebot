"""``x-secret-list``：插件配置里的“密钥列表”字段（Plugin API 1.3）。

插件在 ``config_schema`` 的数组字段上声明::

    "tokens": {
        "type": "array",
        "x-secret-list": {"secret_field": "token", "note_field": "note"},
        "items": {"type": "object", "properties": {"token": {...}, "note": {...}}}
    }

宿主负责：

- 读取时把每项的 ``secret_field`` 换成遮罩（≤24 位：前 4 位···后 4 位；更长：前 10 位···后 10 位），
  并附上稳定的 ``id_field``（默认 ``secret_id``，值为 ``<secret_field>:<sha256 前 10 位>``）；
- 保存时 ``id_field`` 或遮罩命中已保存的条目就保留原密钥，否则当作新填写的明文；空值与重复项丢弃；
- ``legacy_field``（可选，顶层字符串字段）里按行 / 逗号分隔的旧密钥并入列表后清空。

历史上这套逻辑只为 chatgpt_image 写死在 ``api/features.py`` 里；旧版 chatgpt_image 没有声明，
由 :func:`app.plugin_compat.legacy_secret_list_fields` 补一份等价声明。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

DEFAULT_NOTE_FIELD = "note"
DEFAULT_ID_FIELD = "secret_id"
EMPTY_MARKERS = {"", "***"}


def secret_list_specs(schema: Any) -> dict[str, dict[str, str]]:
    """从 config_schema 取出声明了 ``x-secret-list`` 的顶层数组字段 → 规范化声明。"""

    out: dict[str, dict[str, str]] = {}
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    if not isinstance(properties, Mapping):
        return out
    for key, field in properties.items():
        if not isinstance(field, Mapping):
            continue
        spec = normalize_spec(field.get("x-secret-list"), field)
        if spec is not None:
            out[str(key)] = spec
    return out


def normalize_spec(raw: Any, field: Mapping[str, Any] | None = None) -> dict[str, str] | None:
    if raw is None or raw is False:
        return None
    declared: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    secret_field = str(declared.get("secret_field") or "").strip()
    if not secret_field:
        item_props = ((field or {}).get("items") or {}).get("properties") if isinstance(field, Mapping) else None
        if isinstance(item_props, Mapping):
            for candidate in ("token", "secret", "api_key", "key", "value"):
                if candidate in item_props:
                    secret_field = candidate
                    break
    if not secret_field:
        return None
    return {
        "secret_field": secret_field,
        "note_field": str(declared.get("note_field") or DEFAULT_NOTE_FIELD).strip(),
        "id_field": str(declared.get("id_field") or DEFAULT_ID_FIELD).strip(),
        "legacy_field": str(declared.get("legacy_field") or "").strip(),
    }


def secret_id(secret: str, spec: Mapping[str, str]) -> str:
    value = str(secret or "").strip()
    prefix = spec.get("secret_field") or "secret"
    if not value:
        return f"{prefix}:empty"
    return f"{prefix}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:10]}"


def mask_secret(secret: str) -> str:
    value = str(secret or "").strip()
    if not value:
        return ""
    if len(value) <= 24:
        return f"{value[:4]}···{value[-4:]}"
    return f"{value[:10]}···{value[-10:]}"


def entries_from_config(config: Mapping[str, Any], field_key: str, spec: Mapping[str, str]) -> list[dict[str, Any]]:
    """明文配置 → ``[{secret_field: 明文, note_field: 备注, ...其它子字段}]``（去重、去空，含 legacy_field）。"""

    secret_field = spec["secret_field"]
    note_field = spec["note_field"]
    id_field = spec["id_field"]
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(secret: Any, note: Any = "", extra: Mapping[str, Any] | None = None) -> None:
        value = str(secret or "").strip()
        if not value or value in seen or value in EMPTY_MARKERS:
            return
        seen.add(value)
        item: dict[str, Any] = {
            k: v for k, v in dict(extra or {}).items() if k not in {secret_field, note_field, id_field}
        }
        item[secret_field] = value
        item[note_field] = str(note or "").strip()
        entries.append(item)

    raw = config.get(field_key)
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                add(
                    item.get(secret_field) or item.get("accessToken") or item.get("access_token"),
                    item.get(note_field) or item.get("remark") or item.get("source") or "",
                    item,
                )
            else:
                add(item)
    legacy = spec.get("legacy_field")
    if legacy:
        for line in str(config.get(legacy) or "").replace(",", "\n").splitlines():
            add(line)
    return entries


def sanitize(config: Mapping[str, Any], specs: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    """读取用：把密钥列表换成遮罩 + id；``config`` 需为明文（调用方先解密）。只返回被改写的字段。"""

    out: dict[str, Any] = {}
    for field_key, spec in specs.items():
        entries = entries_from_config(config, field_key, spec)
        out[field_key] = [
            {
                **{k: v for k, v in entry.items() if k != spec["secret_field"]},
                spec["secret_field"]: mask_secret(entry[spec["secret_field"]]),
                spec["id_field"]: secret_id(entry[spec["secret_field"]], spec),
            }
            for entry in entries
        ]
        if spec.get("legacy_field"):
            out[spec["legacy_field"]] = ""
    return out


def preserve(
    existing_plain: Mapping[str, Any],
    incoming: Mapping[str, Any],
    specs: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """保存用：把客户端回传的遮罩 / id 换回已保存的明文；新填写的明文原样保留。"""

    merged = dict(incoming)
    for field_key, spec in specs.items():
        raw_items = incoming.get(field_key)
        if not isinstance(raw_items, list):
            continue
        secret_field = spec["secret_field"]
        note_field = spec["note_field"]
        id_field = spec["id_field"]
        existing_entries = entries_from_config(existing_plain, field_key, spec)
        by_id = {secret_id(entry[secret_field], spec): entry[secret_field] for entry in existing_entries}
        by_mask = {mask_secret(entry[secret_field]): entry[secret_field] for entry in existing_entries}
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_items):
            if isinstance(item, Mapping):
                value = str(item.get(secret_field) or "").strip()
                note = str(item.get(note_field) or item.get("remark") or item.get("source") or "").strip()
                ref = str(item.get(id_field) or "").strip()
                extra = {k: v for k, v in item.items() if k not in {secret_field, note_field, id_field}}
            else:
                value, note, ref, extra = str(item or "").strip(), "", "", {}
            secret = by_id.get(ref) or by_mask.get(value) or value
            if secret in EMPTY_MARKERS and index < len(existing_entries):
                secret = existing_entries[index][secret_field]
            if not secret or secret in EMPTY_MARKERS or secret in seen:
                continue
            seen.add(secret)
            normalized.append({**extra, secret_field: secret, note_field: note})
        merged[field_key] = normalized
        if spec.get("legacy_field"):
            merged[spec["legacy_field"]] = ""
    return merged


__all__ = [
    "entries_from_config",
    "mask_secret",
    "normalize_spec",
    "preserve",
    "sanitize",
    "secret_id",
    "secret_list_specs",
]
