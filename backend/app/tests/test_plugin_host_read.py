"""chat.read_state@1 的权限、账号/会话边界及真实 Telethon TL 契约。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telethon import TelegramClient
from telethon.errors import PeerIdInvalidError
from telethon.tl.functions.messages import GetPeerDialogsRequest
from telethon.tl.types import (
    Dialog,
    InputDialogPeer,
    InputPeerChannel,
    InputPeerChat,
    InputPeerUser,
    Message,
    PeerChannel,
    PeerChat,
    PeerNotifySettings,
    PeerUser,
)
from telethon.tl.types.messages import PeerDialogs
from telethon.tl.types.updates import State
from telethon.utils import get_peer_id

from app.worker.plugins import loader
from app.worker.plugins.capabilities import PluginCapabilities
from app.worker.plugins.host_read import HOST_RESOURCE_INDEX, HostReadError, validate_reads
from app.worker.plugins.manifest import Manifest

REF = "chat.read_state@1"


def _dialog(peer, max_id=17):
    return Dialog(
        peer=peer, top_message=30, read_inbox_max_id=max_id, read_outbox_max_id=9,
        unread_count=4, unread_mentions_count=0, unread_reactions_count=0,
        unread_poll_votes_count=0, notify_settings=PeerNotifySettings(),
    )


def _response(peer, max_id=17):
    return PeerDialogs(
        dialogs=[_dialog(peer, max_id)],
        messages=[Message(id=30, peer_id=peer, message="不应暴露的正文")],
        chats=[], users=[], state=State(pts=1, qts=0, date=None, seq=1, unread_count=4),
    )


def _setup(*, reads=(REF,), permissions=("read_chat",), peer=None, input_peer=None):
    peer = peer or PeerUser(42)
    # TelegramClient.__call__ 是异步的；spec 同时确保没有悄悄添加其他能力。
    client = AsyncMock(spec=TelegramClient)
    client.attach_mock(Mock(return_value=True), "is_connected")
    client.attach_mock(AsyncMock(return_value=input_peer or InputPeerUser(42, 123)), "get_input_entity")
    client.return_value = _response(peer)
    state = loader._AccountState(9)
    state.client = client
    manifest = Manifest(key="demo", display_name="示例", reads=list(reads), permissions=list(permissions))
    host = loader._build_plugin_host(state, "demo", manifest, None, None)
    return state, host, client


def _assert_no_telegram(client):
    client.is_connected.assert_not_called()
    client.get_input_entity.assert_not_awaited()
    client.assert_not_awaited()


def test_resource_and_capability_registration():
    assert validate_reads([REF]) == ([REF], [])
    assert HOST_RESOURCE_INDEX[REF].params == ("chat_id",)
    assert HOST_RESOURCE_INDEX[REF].since == "0.118.0-beta.2"
    assert PluginCapabilities().has(f"host.read.{REF}")


async def test_undeclared_read_is_rejected_before_telegram():
    _, host, client = _setup(reads=())
    with pytest.raises(HostReadError, match="未在 plugin.json reads 中声明"):
        await host.read(REF, chat_id=42)
    _assert_no_telegram(client)


@pytest.mark.parametrize("permissions", [(), ("resolve_entity",), ("send_message",)])
async def test_read_chat_permission_is_required(permissions):
    _, host, client = _setup(permissions=permissions)
    assert not host.can_read(REF)
    with pytest.raises(HostReadError, match="当前上下文不提供"):
        await host.read(REF, chat_id=42)
    _assert_no_telegram(client)


@pytest.mark.parametrize("params", [{}, {"chat_id": None}, {"chat_id": True}, {"chat_id": False},
                                   {"chat_id": "42"}, {"chat_id": 42.0}, {"chat_id": 0},
                                   {"chat_id": [42]}, {"chat_id": {"id": 42}}])
async def test_chat_id_must_be_an_integer(params):
    _, host, client = _setup()
    with pytest.raises(HostReadError, match="chat_id 必须是非零整数"):
        await host.read(REF, **params)
    _assert_no_telegram(client)


@pytest.mark.parametrize("extra", [{"account_id": 10}, {"client": object()}, {"mark_read": True}])
async def test_extra_parameters_cannot_select_another_account_or_mutate(extra):
    _, host, client = _setup()
    with pytest.raises(HostReadError, match="不支持参数"):
        await host.read(REF, chat_id=42, **extra)
    _assert_no_telegram(client)


async def test_nonempty_allowlist_denies_other_chats():
    state, host, client = _setup()
    state.ignored_peers = {-100123}
    with pytest.raises(HostReadError, match="不在当前账号允许会话范围"):
        await host.read(REF, chat_id=42)
    _assert_no_telegram(client)


@pytest.mark.parametrize("allowed", [set(), {42}])
@pytest.mark.parametrize("max_id", [0, 17])
async def test_empty_or_matching_allowlist_and_zero_progress(allowed, max_id):
    state, host, client = _setup()
    state.ignored_peers = allowed
    client.return_value = _response(PeerUser(42), max_id)
    assert await host.read(REF, chat_id=42) == {"chat_id": 42, "read_inbox_max_id": max_id}


@pytest.mark.parametrize("peer,input_peer", [
    (PeerUser(42), InputPeerUser(42, 123)),
    (PeerChat(42), InputPeerChat(42)),
    (PeerChannel(42), InputPeerChannel(42, 123)),
])
async def test_real_request_response_and_only_read_operations(peer, input_peer):
    state, host, client = _setup(peer=peer, input_peer=input_peer)
    chat_id = get_peer_id(peer)
    state.ignored_peers = {chat_id}
    result = await host.read(REF, chat_id=chat_id)
    assert result == {"chat_id": chat_id, "read_inbox_max_id": 17}
    assert all(type(v) is int for v in result.values())
    client.get_input_entity.assert_awaited_once_with(chat_id)
    client.assert_awaited_once()
    request = client.await_args.args[0]
    assert type(request) is GetPeerDialogsRequest
    assert len(request.peers) == 1
    assert type(request.peers[0]) is InputDialogPeer
    assert request.peers[0].peer is input_peer
    assert bytes(request)  # 真正可序列化的 TL 请求，不是自造 dict。
    assert [call[0] for call in client.mock_calls] == ["is_connected", "get_input_entity", ""]


async def test_live_current_account_client_and_scope_are_used():
    state, host, original_client = _setup()
    other_state, _, other_client = _setup()
    other_state.account_id = 10
    _, _, replacement = _setup()
    replacement.return_value = _response(PeerUser(42), 99)
    state.client = replacement
    assert (await host.read(REF, chat_id=42))["read_inbox_max_id"] == 99
    _assert_no_telegram(original_client)
    _assert_no_telegram(other_client)
    state.ignored_peers = {43}  # 模拟热更新替换整个集合。
    with pytest.raises(HostReadError, match="不在当前账号允许会话范围"):
        await host.read(REF, chat_id=42)
    replacement.assert_awaited_once()


@pytest.mark.parametrize("missing", [True, False])
async def test_missing_or_disconnected_userbot_fails(missing):
    state, host, client = _setup()
    if missing:
        state.client = None
    else:
        client.is_connected.return_value = False
    with pytest.raises(HostReadError, match="UserBot 未连接"):
        await host.read(REF, chat_id=42)
    client.get_input_entity.assert_not_awaited()
    client.assert_not_awaited()


@pytest.mark.parametrize("dialogs", [[], [_dialog(PeerUser(43))]])
async def test_absent_or_unmatched_dialog_is_not_zero_progress(dialogs):
    _, host, client = _setup()
    client.return_value.dialogs = dialogs
    with pytest.raises(HostReadError, match="会话不存在或不可访问"):
        await host.read(REF, chat_id=42)


async def test_matching_dialog_is_selected_instead_of_first():
    _, host, client = _setup()
    client.return_value.dialogs.insert(0, _dialog(PeerUser(43), 999))
    assert await host.read(REF, chat_id=42) == {"chat_id": 42, "read_inbox_max_id": 17}


@pytest.mark.parametrize("max_id", [None, -1, True, "17"])
async def test_invalid_progress_fails(max_id):
    _, host, client = _setup()
    client.return_value = _response(PeerUser(42), max_id)
    with pytest.raises(HostReadError, match="无效的已读进度"):
        await host.read(REF, chat_id=42)


async def test_resolution_mismatch_cannot_query_another_chat():
    _, host, client = _setup(input_peer=InputPeerUser(43, 123))
    with pytest.raises(HostReadError, match="无法解析指定会话"):
        await host.read(REF, chat_id=42)
    client.assert_not_awaited()


@pytest.mark.parametrize("stage", ["connection", "resolve", "request", "response"])
async def test_errors_are_sanitized_and_never_return_zero(stage):
    _, host, client = _setup()
    secret = "敏感凭据与正文"
    if stage == "connection":
        client.is_connected.side_effect = RuntimeError(secret)
    elif stage == "resolve":
        client.get_input_entity.side_effect = ValueError(secret)
    elif stage == "request":
        client.side_effect = PeerIdInvalidError(request=GetPeerDialogsRequest(peers=[]))
    else:
        client.return_value = SimpleNamespace(dialogs=None, secret=secret)
    with pytest.raises(HostReadError, match="查询失败") as exc:
        await host.read(REF, chat_id=42)
    assert secret not in str(exc.value)
    if stage in {"connection", "resolve"}:
        client.assert_not_awaited()
