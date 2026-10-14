"""System Agent 密钥抽取与打码。"""

from __future__ import annotations

import pytest

from app.services.system_agent.secrets import (
    MAX_SECRET_MAP_ENTRIES,
    apply_secret_placeholders,
    build_secret_map,
    cap_secret_map,
    contains_request_header_context,
    decrypt_secret_map,
    encrypt_secret_map,
    encrypt_secrets_dict,
    extract_plaintext_secrets,
    find_credential_placeholders,
    is_credential_placeholder,
    looks_like_provider_credential_paste,
    looks_like_standalone_provider_key,
    make_placeholder,
    merge_secret_into_arguments,
    placeholders_remain,
    redact_known_secrets,
    redact_user_message,
    restore_placeholders,
)


def test_extract_openai_style_key() -> None:
    text = "帮我加一个 provider，key 是 sk-abcdefghijklmnopqrstuvwxyz123456"
    secrets = extract_plaintext_secrets(text)
    assert secrets
    assert secrets[0].startswith("sk-")


def test_extract_labeled_key() -> None:
    text = "api_key: my-super-secret-token-value"
    secrets = extract_plaintext_secrets(text)
    assert any("my-super-secret-token-value" in s for s in secrets)


@pytest.mark.parametrize(
    "key",
    (
        "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        "abcdefghijklmnopqrstuvwxyz123456",
        "abcdefghijkl.mnopqrstuvwxyz",
    ),
)
def test_extract_and_redact_standalone_provider_key(key: str) -> None:
    secrets = extract_plaintext_secrets(key)

    assert looks_like_standalone_provider_key(key) is True
    assert secrets == [key]
    assert key not in redact_known_secrets(key, secrets)


def test_standalone_provider_key_rejects_long_sentence() -> None:
    text = "issue-abcdefghijklmnopqrstuvwxyz123456 不是 Provider Key"

    assert looks_like_standalone_provider_key(text) is False


@pytest.mark.parametrize(
    "key",
    (
        "Abcdefghijklmnopqrstuvwxyz",
        "AbcdEFGHijklmnop+/=_-1234",
    ),
)
def test_extract_json_labeled_provider_key(key: str) -> None:
    text = f'{{"baseurl":"https://models.example/v1","apikey":"{key}"}}'

    secrets = extract_plaintext_secrets(text)

    assert key in secrets
    assert looks_like_provider_credential_paste(text) is True
    assert key not in redact_known_secrets(text, secrets)


@pytest.mark.parametrize(
    "key",
    (
        "AbCdEfGhIjKlMnOpQrStUvWxYz123456",
        "abcdefghijklmnop.qrstuvwxyz123456",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
    ),
)
def test_extract_provider_paste_keys_that_router_accepts(key: str) -> None:
    text = f"https://api.example/v1 {key}"

    secrets = extract_plaintext_secrets(text)
    redacted = redact_known_secrets(text, secrets)

    assert secrets == [key]
    assert key not in redacted
    assert "[REDACTED]" in redacted


def test_redact_known_secrets() -> None:
    key = "sk-abcdefghijklmnopqrstuvwxyz123456"
    text = f"这是密钥 {key} 请保存"
    out = redact_known_secrets(text, [key])
    assert key not in out
    assert "REDACTED" in out or "***" in out or "sk-" not in out


def test_proxy_url_password_is_extracted_and_redacted() -> None:
    text = "添加 socks5://proxy-user:VerySecret123@example.com:1080 这个代理"

    secrets = extract_plaintext_secrets(text)

    assert secrets == ["VerySecret123"]
    assert "VerySecret123" not in redact_known_secrets(text, secrets)


def test_telegram_bot_token_is_extracted_and_redacted() -> None:
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    text = f"用这个 Bot Token 配置通知：{token}"

    secrets = extract_plaintext_secrets(text)

    assert secrets == [token]
    assert token not in redact_known_secrets(text, secrets)


@pytest.mark.parametrize(
    "text",
    (
        'request_headers=[{"name":"X-Tenant-ID","value":"opaque-header-secret"}]',
        "请求头 X-Tenant-ID 的 value 为 tenant",
        "请设置兼容请求头 X-Tenant-ID 为 opaque-header-secret",
        "request_headers: X-Tenant-ID=opaque-header-secret",
        "兼容请求头：X-Tenant-ID: opaque-header-secret",
        "Please set HTTP header X-Tenant-ID to opaque-header-secret",
        "Set custom header X-Tenant-ID=opaque-header-secret",
        "Set compatibility headers for this provider",
    ),
)
def test_request_header_configuration_is_blocked_from_chat(text: str) -> None:
    redacted = redact_user_message(text)

    assert contains_request_header_context(text) is True
    assert "opaque-header-secret" not in redacted
    assert "tenant" not in redacted
    assert "Provider 设置" in redacted


def test_merge_secret_into_arguments_from_chat() -> None:
    public, secrets, fields = merge_secret_into_arguments(
        {"name": "p1", "default_model": "m"},
        secret_names=("api_key",),
        chat_secrets=["sk-abcdefghijklmnopqrstuvwxyz123456"],
    )
    assert public.get("has_api_key") is True
    assert "api_key" not in public
    assert secrets["api_key"].startswith("sk-")
    assert fields == ["api_key"]


def test_chat_secret_replaces_model_redacted_placeholder() -> None:
    public, secrets, fields = merge_secret_into_arguments(
        {"base_url": "https://api.example/v1", "api_key": "[REDACTED]"},
        secret_names=("api_key",),
        chat_secrets=["sk-abcdefghijklmnopqrstuvwxyz123456"],
    )

    assert public["has_api_key"] is True
    assert "api_key" not in public
    assert secrets == {"api_key": "sk-abcdefghijklmnopqrstuvwxyz123456"}
    assert fields == ["api_key"]


def test_chat_secret_does_not_fill_another_secret_when_one_is_already_present() -> None:
    public, secrets, fields = merge_secret_into_arguments(
        {
            "base_url": "https://api.example/v1",
            "api_key": "sk-already-present-secret-value",
        },
        secret_names=("api_key", "request_headers"),
        chat_secrets=["sk-already-present-secret-value"],
    )

    assert public["has_api_key"] is True
    assert public.get("has_request_headers") is not True
    assert secrets == {"api_key": "sk-already-present-secret-value"}
    assert fields == ["api_key"]


# ─────────── WP-B5：密钥占位符往返 ───────────


def test_placeholder_format_is_ascii_and_not_secret_shaped() -> None:
    """`[[TP-CRED-n]]` 不能被密钥/敏感标签正则命中，否则会自我打码。"""
    token = make_placeholder(1)
    assert token == "[[TP-CRED-1]]"
    assert token.isascii()
    assert not extract_plaintext_secrets(f"请用 {token} 调用")
    assert not contains_request_header_context(token)
    # 通过基础打码后仍然原样保留（不变成 [REDACTED]）
    assert redact_user_message(f"请用 {token} 调用") == f"请用 {token} 调用"
    # 旧占位符集合也认它（模型回填到敏感字段时不会被当成真实值）
    assert is_credential_placeholder(token)


def test_build_secret_map_is_deterministic_and_ordered() -> None:
    secret_map = build_secret_map(["aaa", "bbb"])
    assert secret_map == {"[[TP-CRED-1]]": "aaa", "[[TP-CRED-2]]": "bbb"}
    assert build_secret_map([]) == {}


def test_is_credential_placeholder_requires_full_token() -> None:
    assert is_credential_placeholder("[[TP-CRED-7]]")
    assert is_credential_placeholder("  [[TP-CRED-7]]  ")
    assert not is_credential_placeholder("[[TP-CRED-7]] more text")
    assert not is_credential_placeholder("[[TP-CRED-]]")
    assert not is_credential_placeholder("[[TP-CRED-x]]")
    assert not is_credential_placeholder("")
    assert not is_credential_placeholder(None)


def test_find_credential_placeholders_dedupes_in_order() -> None:
    assert find_credential_placeholders("a [[TP-CRED-2]] b [[TP-CRED-1]] a [[TP-CRED-2]]") == [
        "[[TP-CRED-2]]",
        "[[TP-CRED-1]]",
    ]
    assert find_credential_placeholders("no placeholders here") == []


def test_cap_secret_map_keeps_newest_entries() -> None:
    secret_map = build_secret_map([str(i) for i in range(1, 30)])
    assert len(secret_map) == 29
    capped = cap_secret_map(secret_map)
    assert len(capped) == MAX_SECRET_MAP_ENTRIES
    # FIFO：最早的被淘汰，最后 20 个保留
    assert capped[make_placeholder(10)] == "10"
    assert make_placeholder(9) not in capped
    assert capped[make_placeholder(29)] == "29"
    # 未超限时原样返回（副本）
    small = build_secret_map(["a"])
    assert cap_secret_map(small) == small
    assert cap_secret_map(small) is not small


def test_restore_placeholders_replaces_every_occurrence() -> None:
    secret_map = build_secret_map(["sk-real-value", "tok-real"])
    text = "用 [[TP-CRED-1]] 调，失败用 [[TP-CRED-2]]；再不行还试 [[TP-CRED-1]]。"
    assert restore_placeholders(text, secret_map) == (
        "用 sk-real-value 调，失败用 tok-real；再不行还试 sk-real-value。"
    )


def test_restore_placeholders_keeps_unknown_placeholder_intact() -> None:
    """映射里没有的占位符必须原样保留，不能打码或丢弃整段文本。"""
    secret_map = build_secret_map(["sk-real-value"])
    text = "新密钥 [[TP-CRED-1]]，旧密钥 [[TP-CRED-9]] 已过期。"
    assert restore_placeholders(text, secret_map) == (
        "新密钥 sk-real-value，旧密钥 [[TP-CRED-9]] 已过期。"
    )


@pytest.mark.parametrize("text", ("", None, "普通文本"))
def test_restore_placeholders_noop_without_map_or_token(text) -> None:
    assert restore_placeholders(text, {}) == (text or "")
    assert restore_placeholders(text, None) == (text or "")
    assert restore_placeholders("[[TP-CRED-1]]", None) == "[[TP-CRED-1]]"


def test_secret_map_encrypt_roundtrip_and_reject_garbage() -> None:
    secret_map = build_secret_map(["sk-abcdefghijklmnopqrstuvwxyz123456"])
    token = encrypt_secret_map(secret_map)
    assert token
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in token
    assert decrypt_secret_map(token) == secret_map
    # 解密失败（MASTER_KEY 变更 / 数据损坏）→ 空映射，显示边界退化为占位符
    assert decrypt_secret_map("not-a-fernet-token") == {}
    assert decrypt_secret_map(None) == {}


def test_decrypt_secret_map_drops_non_placeholder_keys() -> None:
    token = encrypt_secrets_dict({"not-a-placeholder": "sk-x", "[[TP-CRED-1]]": "sk-real"})
    assert decrypt_secret_map(token) == {"[[TP-CRED-1]]": "sk-real"}


def test_apply_secret_placeholders_replaces_only_mapped_secrets() -> None:
    secret_map = build_secret_map(["sk-abcdefghijklmnopqrstuvwxyz123456"])
    text = (
        "key=sk-abcdefghijklmnopqrstuvwxyz123456  "
        "其它内容 sk-other-unmapped-key-123456789012 不变"
    )
    out = apply_secret_placeholders(text, secret_map)
    assert "key=[[TP-CRED-1]]" in out
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in out
    assert "sk-other-unmapped-key-123456789012" in out


def test_placeholders_remain_detects_leftover_tokens() -> None:
    assert placeholders_remain("还剩 [[TP-CRED-3]]")
    assert not placeholders_remain("已经还原了")
    assert not placeholders_remain("")
