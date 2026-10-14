"""阶段 F 复审修复 #4：插件显式 route="auto" 必须复用共享 Router。

契约：对同一段内容（如代码提示），模板路由（``llm_router.pick_provider``）与插件
``ctx.ai`` 的显式 ``route="auto"`` 应选出**同一个** Provider——即插件 auto 不再只挑
最便宜的 chat，而是走内容路由（code/math/vision/reason 规则）。

同时验证：
- 插件 auto **禁用**分类器与全局 fallback（宿主级能力，插件不可用）。
- 旧参数全缺省的"推断 auto"保持向后兼容（chat 优先 / cost_tier 升序），不引入内容路由。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import llm_router
from app.services.llm_dto import LLMProviderDTO
from app.services.llm_router import pick_provider
from app.worker.plugins import ai_facade

_CODE_PROMPT = "帮我写一个 python 函数 def foo(): 返回斐波那契数列"


def _dto(
    provider_id: int,
    *,
    name: str,
    tags: list[str],
    cost_tier: int,
    pool_only: bool = False,
) -> LLMProviderDTO:
    return LLMProviderDTO(
        id=provider_id,
        name=name,
        provider="openai",
        api_format="chat_completions",
        base_url="https://example/v1",
        default_model=f"{name}-model",
        api_key_enc="enc",
        keys=(
            # pool_only：没有主 Key，只有一条启用的池内 Key（`has_api_key` 仍为 True）
            [
                {
                    "id": 1,
                    "label": "pool",
                    "key_hint": "***tail",
                    "enabled": True,
                    "api_key_enc": "pool-enc",
                }
            ]
            if pool_only
            else []
        ),
        modality="text",
        tags=tags,
        cost_tier=cost_tier,
        models=[{"id": f"{name}-model", "enabled": True}],
    )


def _pool_only_coder() -> LLMProviderDTO:
    dto = _dto(2, name="coder", tags=["code"], cost_tier=3, pool_only=True)
    dto.api_key_enc = None  # dataclass 运行时字段，置空以表达"只用密钥池"
    return dto


# ─────────── WP-B9：池内 Key Provider 不应从 auto 候选消失 ───────────


@pytest.mark.asyncio
async def test_plugin_auto_keeps_pool_only_provider_candidate() -> None:
    """pool-only（无主 Key、有启用池内 Key）的 code Provider 必须仍是 auto 候选。

    此前 `_shared_router_auto` 用 `to_dict()` + 手补 `api_key_enc` 构造 Router 池，
    展示版 dict 的 `keys[]` 没有 `api_key_enc`，共享 Router 的 `_has_api_key`
    会把这类 Provider 全部过滤掉，插件 auto 永远选不到它。
    """
    chat = _dto(1, name="chat", tags=["chat"], cost_tier=1)
    coder = _pool_only_coder()
    assert chat.has_api_key is True
    assert coder.has_api_key is True
    assert coder.api_key_enc is None  # 确认造的是 pool-only

    dto, tag, mode = await ai_facade._resolve_route(
        {1: chat, 2: coder},
        provider=None,
        provider_tag=None,
        route="auto",
        user_content=_CODE_PROMPT,
    )

    assert mode == "auto"
    assert dto.id == 2
    assert tag == "code"


@pytest.mark.asyncio
async def test_plugin_auto_pool_only_matches_shared_router() -> None:
    """与共享 Router 口径一致：池内 Provider 在模板路由侧同样是候选。"""
    chat = _dto(1, name="chat", tags=["chat"], cost_tier=1)
    coder = _pool_only_coder()
    providers = {1: chat, 2: coder}

    # 共享 Router 侧的池也必须带池内 `keys[].api_key_enc`
    pool = {int(d.id): d.to_runtime_dict() for d in providers.values()}
    decision = await pick_provider(_CODE_PROMPT, None, False, pool)
    plugin_dto, _tag, _mode = await ai_facade._resolve_route(
        providers,
        provider=None,
        provider_tag=None,
        route="auto",
        user_content=_CODE_PROMPT,
    )

    assert decision.provider_id == 2
    assert plugin_dto.id == 2


@pytest.mark.asyncio
async def test_shared_router_auto_pool_carries_pool_key_ciphertext() -> None:
    """内部池构造：池内 Key 密文必须传给 Router（它靠这个判断可用性）。"""
    chat = _dto(1, name="chat", tags=["chat"], cost_tier=1)
    coder = _pool_only_coder()
    captured: dict[int, dict] = {}

    class _StubRouter:
        @staticmethod
        async def pick_provider(_text, _repl, _photo, pool, **_kwargs):  # noqa: ANN001
            captured.update(pool)
            return SimpleNamespace(provider_id=1, matched_tag="chat")

    with patch.object(llm_router, "pick_provider", _StubRouter.pick_provider):
        # Router 返回 id=1（chat），我们只关心构造出的池内容
        assert await ai_facade._shared_router_auto([chat, coder], _CODE_PROMPT) is not None

    entries = captured.get(2, {}).get("keys") or []
    assert entries and entries[0].get("api_key_enc") == "pool-enc"


def _router_pool(dtos: list[LLMProviderDTO]) -> dict[int, dict]:
    pool: dict[int, dict] = {}
    for dto in dtos:
        d = dto.to_dict()
        d["api_key_enc"] = dto.api_key_enc
        pool[int(dto.id)] = d
    return pool


@pytest.mark.asyncio
async def test_template_and_plugin_auto_pick_same_provider_for_code() -> None:
    """代码内容：模板 Router 与插件显式 auto 应选同一个 code Provider。"""
    chat = _dto(1, name="chat", tags=["chat"], cost_tier=1)
    coder = _dto(2, name="coder", tags=["code"], cost_tier=3)
    dtos = [chat, coder]

    # 模板路由（共享 Router）。
    template_decision = await pick_provider(_CODE_PROMPT, None, False, _router_pool(dtos))

    # 插件显式 route="auto"。
    providers = {int(d.id): d for d in dtos}
    plugin_dto, plugin_tag, plugin_mode = await ai_facade._resolve_route(
        providers,
        provider=None,
        provider_tag=None,
        route="auto",
        user_content=_CODE_PROMPT,
    )

    assert plugin_mode == "auto"
    # 关键断言：二者选出同一个 Provider（都命中 code Provider，而不是便宜的 chat）。
    assert plugin_dto.id == template_decision.provider_id == 2
    assert plugin_tag == "code"


@pytest.mark.asyncio
async def test_plugin_inferred_auto_stays_cheap_chat_backward_compatible() -> None:
    """旧行为：不传 route（推断 auto）仍走 chat 优先 / cost_tier 升序，不做内容路由。"""
    chat = _dto(1, name="chat", tags=["chat"], cost_tier=1)
    coder = _dto(2, name="coder", tags=["code"], cost_tier=3)
    providers = {1: chat, 2: coder}

    dto, _tag, mode = await ai_facade._resolve_route(
        providers,
        provider=None,
        provider_tag=None,
        route=None,  # 推断 auto
        user_content=_CODE_PROMPT,  # 即使给了代码内容，推断 auto 也不做内容路由
    )
    assert mode == "auto"
    # 推断 auto 保持便宜 chat（向后兼容），不因内容是代码而切到 coder。
    assert dto.id == 1


@pytest.mark.asyncio
async def test_plugin_auto_does_not_use_classifier_or_fallback() -> None:
    """插件 auto 只在候选内做内容路由；无 code 命中时回落到 chat 兜底，不调用分类器。"""
    # 只有 chat / general，无 code 标签：代码内容也无处可选 → 回落 chat 兜底。
    chat = _dto(1, name="chat", tags=["chat"], cost_tier=1)
    general = _dto(2, name="general", tags=["general"], cost_tier=2)
    providers = {1: chat, 2: general}

    dto, _tag, mode = await ai_facade._resolve_route(
        providers,
        provider=None,
        provider_tag=None,
        route="auto",
        user_content=_CODE_PROMPT,
    )
    assert mode == "auto"
    # 无 code Provider 时共享 Router 回落到便宜候选；chat 优先。
    assert dto.id == 1
