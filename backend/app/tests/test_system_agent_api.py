"""System Agent HTTP API 层：会话所有权与 NDJSON 错误路径。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import system_agent as api
from app.schemas.system_agent import SystemAgentImageAttachment


class _FakeSvc:
    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.messages: dict[str, list[Any]] = {}
        self.last_stream_kwargs: dict[str, Any] = {}
        self.config = {
            "enabled": False,
            "provider_id": None,
            "model": None,
            "fallback_provider_ids": [],
            "require_tool_approval": False,
            "max_steps": 8,
            "max_tool_calls": 24,
            "session_token_limit": 16384,
        }

    async def get_config(self, _db):
        return dict(self.config)

    async def update_config(self, _db, patch):
        self.config.update({k: v for k, v in patch.items() if v is not None or k in patch})
        return dict(self.config)

    async def get_capabilities(self, _db, *, channel, role):
        return {
            "enabled": self.config["enabled"],
            "provider_id": self.config["provider_id"],
            "model": self.config["model"],
            "ai_enabled": True,
            "timezone": "UTC",
            "tools": [{"name": "system.get_context", "available": True, "read_only": True}],
            "stage": 1,
            "write_tools_available": False,
        }

    async def create_session(self, _db, **kwargs):
        session = SimpleNamespace(
            id="s1",
            web_user_id=kwargs.get("web_user_id"),
            bot_tg_user_id=None,
            account_id=kwargs.get("account_id"),
            channel=kwargs["channel"],
            title=kwargs.get("title"),
            status="active",
            created_at=None,
            updated_at=None,
        )
        self.sessions[session.id] = session
        return session

    async def list_sessions(self, _db, **kwargs):
        uid = kwargs.get("web_user_id")
        include_bot = bool(kwargs.get("include_bot_sessions"))
        return [
            session
            for session in self.sessions.values()
            if session.web_user_id == uid or (include_bot and session.channel == "bot")
        ]

    async def get_session(self, _db, session_id, **kwargs):
        s = self.sessions.get(session_id)
        if s is None:
            return None
        if kwargs.get("web_user_id") is not None and s.web_user_id != kwargs["web_user_id"]:
            if not (kwargs.get("allow_bot_session") and s.channel == "bot"):
                return None
        return s

    async def update_session(self, _db, session, **kwargs):
        for k, v in kwargs.items():
            if v is not ... and hasattr(session, k):
                setattr(session, k, v)
        return session

    async def delete_session(self, _db, session):
        self.sessions.pop(session.id, None)

    async def delete_all_sessions(self, _db, *, web_user_id):
        ids = [sid for sid, s in self.sessions.items() if s.web_user_id == web_user_id]
        for sid in ids:
            self.sessions.pop(sid, None)
        return len(ids)

    async def list_messages(self, _db, session_id, **kwargs):
        return self.messages.get(session_id, [])

    async def reconcile_stale_messages(self, _db, _session_id):
        return 0

    async def get_message(self, _db, message_id, *, session_id):
        return next(
            (
                message
                for message in self.messages.get(session_id, [])
                if message.id == message_id
            ),
            None,
        )

    async def is_latest_completed_pair(
        self,
        _db,
        *,
        session_id,
        user_message_id,
        assistant_message_id,
    ):
        rows = self.messages.get(session_id, [])
        users = [message for message in rows if message.role == "user"]
        if not users:
            return False
        latest_user = max(users, key=lambda message: message.id)
        assistants = sorted(
            (
                message
                for message in rows
                if message.role == "assistant" and message.id > latest_user.id
            ),
            key=lambda message: message.id,
        )
        return bool(
            latest_user.id == user_message_id
            and latest_user.run_status == "succeeded"
            and assistants
            and assistants[0].id == assistant_message_id
            and assistants[0].run_status == "completed"
        )

    async def stream_message(self, _db, **kwargs):
        self.last_stream_kwargs = dict(kwargs)
        yield {
            "type": "run_started",
            "run_id": "r1",
            "session_id": kwargs["session"].id,
            "seq": 1,
        }
        yield {
            "type": "assistant_message",
            "content": "ok",
            "run_id": "r1",
            "session_id": kwargs["session"].id,
            "seq": 2,
        }
        yield {"type": "done", "ok": True, "run_id": "r1", "session_id": kwargs["session"].id, "seq": 3}


class _FakeRunManager:
    def __init__(self) -> None:
        self.last_start_kwargs: dict[str, Any] = {}
        self.last_input_kwargs: dict[str, Any] = {}
        self.last_stop_replace_kwargs: dict[str, Any] = {}
        self.broken_stream = False

    async def start_run(self, **kwargs):
        self.last_start_kwargs = dict(kwargs)
        return SimpleNamespace(
            id="durable-r1",
            session_id=kwargs["session_id"],
            web_user_id=kwargs["web_user_id"],
            user_message_id=(
                kwargs.get("regenerate_message_id")
                or kwargs.get("retry_message_id")
                or 101
            ),
            client_request_id=kwargs["client_request_id"],
            kind=(
                "regenerate"
                if kwargs.get("regenerate_message_id")
                else "retry"
                if kwargs.get("retry_message_id")
                else "message"
            ),
            status="running",
            last_seq=0,
            cancel_requested=False,
            error_code=None,
            error_message=None,
            started_at=None,
            finished_at=None,
            created_at=None,
            updated_at=None,
        )

    async def stream_events(self, run_id, *, after_seq=0):
        if self.broken_stream:
            raise RuntimeError("database failed with sk-sensitive-secret-value")
        yield {"type": "run_started", "run_id": run_id, "session_id": "s1", "seq": after_seq + 1}
        yield {"type": "assistant_message", "content": "ok", "run_id": run_id, "session_id": "s1", "seq": after_seq + 2}
        yield {"type": "done", "ok": True, "run_id": run_id, "session_id": "s1", "seq": after_seq + 3}

    async def list_runs(self, **kwargs):
        self.last_list_kwargs = dict(kwargs)
        return [
            await self.start_run(
                session_id="s1",
                web_user_id=kwargs["web_user_id"],
                client_request_id="request-listed-1",
            )
        ]

    async def add_run_input(self, run_id, **kwargs):
        self.last_input_kwargs = {"run_id": run_id, **kwargs}
        return SimpleNamespace(
            id=41,
            run_id=run_id,
            kind=kwargs["kind"],
            status="pending",
            client_request_id=kwargs["client_request_id"],
            created_at=None,
            applied_at=None,
        )

    async def stop_and_replace(self, run_id, **kwargs):
        self.last_stop_replace_kwargs = {"run_id": run_id, **kwargs}
        return await self.start_run(
            session_id="s1",
            web_user_id=kwargs["web_user_id"],
            client_request_id=kwargs["client_request_id"],
        )

    async def get_queue_ahead(self, run_id):
        self.last_queue_ahead_run = run_id
        return 2

    async def replace_with_queue_item(self, turn_id, **kwargs):
        self.last_queue_replace_kwargs = {"turn_id": turn_id, **kwargs}
        row = await self.start_run(
            session_id="s1",
            web_user_id=kwargs["web_user_id"],
            client_request_id="request-queued",
        )
        row.status = "queued"
        return row


@pytest.fixture
def fake_svc(monkeypatch):
    svc = _FakeSvc()
    monkeypatch.setattr(api, "get_system_agent_service", lambda: svc)
    return svc


@pytest.fixture
def fake_run_manager(monkeypatch):
    manager = _FakeRunManager()
    monkeypatch.setattr(api, "get_system_agent_run_manager", lambda: manager)
    return manager


@pytest.mark.asyncio
async def test_create_and_list_sessions(fake_svc) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    created = await api.create_session(
        api.SystemAgentSessionCreate(account_id=3, title="hi"),
        db,
        user,
    )
    assert created.id == "s1"
    assert created.account_id == 3
    rows = await api.list_sessions(db, user)
    assert len(rows) == 1
    assert rows[0].web_user_id == 7


@pytest.mark.asyncio
async def test_admin_can_read_but_not_mutate_bot_sessions(fake_svc) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    bot_session = SimpleNamespace(
        id="bot-s1",
        web_user_id=None,
        bot_tg_user_id=1682400007,
        account_id=3,
        channel="bot",
        title="Telegram 排障",
        origin="interactive",
        status="active",
        memory_summary="",
        memory_state={},
        created_at=None,
        updated_at=None,
    )
    fake_svc.sessions[bot_session.id] = bot_session
    fake_svc.messages[bot_session.id] = [
        SimpleNamespace(
            id=9,
            session_id=bot_session.id,
            role="user",
            content={"text": "检查日志"},
            usage=None,
            run_status="completed",
            error_code=None,
            error_message=None,
            retry_count=0,
            created_at=None,
        )
    ]

    web_only = await api.list_sessions(
        db,
        user,
        status="active",
        origin=None,
        include_bot=False,
        limit=50,
    )
    assert web_only == []

    sessions = await api.list_sessions(
        db,
        user,
        status="active",
        origin=None,
        include_bot=True,
        limit=50,
    )
    assert [session.id for session in sessions] == ["bot-s1"]
    assert sessions[0].bot_tg_user_id == 1682400007

    session = await api.get_session("bot-s1", db, user)
    assert session.channel == "bot"
    messages = await api.list_messages("bot-s1", db, user, limit=100, before_id=None)
    assert [message.content["text"] for message in messages] == ["检查日志"]

    with pytest.raises(HTTPException) as update_error:
        await api.update_session(
            "bot-s1",
            api.SystemAgentSessionUpdate(title="不应修改"),
            db,
            user,
        )
    assert update_error.value.status_code == 404

    with pytest.raises(HTTPException) as delete_error:
        await api.delete_session("bot-s1", db, user)
    assert delete_error.value.status_code == 404


@pytest.mark.asyncio
async def test_get_session_not_found(fake_svc) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=1)
    with pytest.raises(HTTPException) as ei:
        await api.get_session("missing", db, user)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_patch_config(fake_svc) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=1)
    out = await api.patch_config(
        api.SystemAgentConfigPatch(enabled=True, provider_id=2, model="tools-m"),
        db,
        user,
    )
    assert out.enabled is True
    assert out.provider_id == 2
    assert out.model == "tools-m"
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_capabilities_stage1(fake_svc, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.system_agent.plugin_tools.refresh_plugin_system_agent_tools",
        AsyncMock(return_value=[]),
    )
    db = AsyncMock()
    user = SimpleNamespace(id=1)
    caps = await api.get_capabilities(db, user)
    assert caps.stage == 1
    assert caps.write_tools_available is False
    assert caps.tools


@pytest.mark.asyncio
async def test_stream_message_ndjson(fake_svc, fake_run_manager) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(), db, user)
    response = await api.stream_message(
        "s1",
        api.SystemAgentMessageCreate(content="你好"),
        db,
        user,
    )
    assert response.media_type == "application/x-ndjson"
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
    body = "".join(chunks)
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert len(lines) >= 3
    events = [json.loads(ln) for ln in lines]
    types = [e["type"] for e in events]
    assert "run_started" in types
    assert "assistant_message" in types
    assert "done" in types
    db.commit.assert_awaited()


class _IdempotentStreamRunManager:
    """模拟 run_manager 的 client_request_id 去重语义（_find_by_request）。"""

    def __init__(self) -> None:
        self.runs_by_request: dict[str, str] = {}
        self.start_calls: list[str] = []

    async def start_run(self, **kwargs):
        request_id = str(kwargs["client_request_id"])
        self.start_calls.append(request_id)
        run_id = self.runs_by_request.get(request_id)
        if run_id is None:
            run_id = f"stream-run-{len(self.runs_by_request) + 1}"
            self.runs_by_request[request_id] = run_id
        return SimpleNamespace(id=run_id)

    async def stream_events(self, run_id, *, after_seq=0):
        yield {"type": "run_started", "run_id": run_id, "session_id": "s1", "seq": after_seq + 1}
        yield {"type": "done", "ok": True, "run_id": run_id, "session_id": "s1", "seq": after_seq + 2}


async def _drain_stream(response) -> None:  # noqa: ANN001
    async for _chunk in response.body_iterator:
        pass


@pytest.mark.asyncio
async def test_stream_run_reuses_client_request_id_across_retries(fake_svc, monkeypatch) -> None:
    # 网络重试/双击：同一 client_request_id 的流式建 run 请求应复用同一 run，
    # 服务端不得每次现造 uuid 导致重复创建 run、重复消耗 LLM token
    manager = _IdempotentStreamRunManager()
    monkeypatch.setattr(api, "get_system_agent_run_manager", lambda: manager)
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(), db, user)

    for _ in range(2):
        response = await api.stream_message(
            "s1",
            api.SystemAgentMessageCreate(content="你好", client_request_id="request-stream-1"),
            db,
            user,
        )
        await _drain_stream(response)

    assert manager.start_calls == ["request-stream-1", "request-stream-1"]
    assert len(manager.runs_by_request) == 1


@pytest.mark.asyncio
async def test_retry_stream_reuses_client_request_id(fake_svc, monkeypatch) -> None:
    manager = _IdempotentStreamRunManager()
    monkeypatch.setattr(api, "get_system_agent_run_manager", lambda: manager)
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(), db, user)
    fake_svc.messages["s1"] = [
        SimpleNamespace(id=9, session_id="s1", role="user", run_status="failed")
    ]

    for _ in range(2):
        response = await api.retry_message(
            "s1",
            9,
            api.SystemAgentMessageRetry(client_request_id="request-retry-stream-1"),
            db,
            user,
        )
        await _drain_stream(response)

    assert manager.start_calls == ["request-retry-stream-1", "request-retry-stream-1"]
    assert len(manager.runs_by_request) == 1


@pytest.mark.asyncio
async def test_stream_run_generates_request_id_when_payload_missing(
    fake_svc,
    fake_run_manager,
) -> None:
    # 兼容旧客户端：不传 client_request_id 时服务端仍现造 uuid 兜底
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(), db, user)

    response = await api.stream_message(
        "s1",
        api.SystemAgentMessageCreate(content="你好"),
        db,
        user,
    )
    await _drain_stream(response)

    request_id = fake_run_manager.last_start_kwargs["client_request_id"]
    assert isinstance(request_id, str)
    assert len(request_id) >= 8


@pytest.mark.asyncio
async def test_start_durable_run_returns_run_and_message_identity(
    fake_svc,
    fake_run_manager,
) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(), db, user)

    out = await api.start_system_agent_run(
        "s1",
        api.SystemAgentRunCreate(
            content="交互里有哪些规则？",
            client_request_id="request-api-1",
        ),
        db,
        user,
    )

    assert out.id == "durable-r1"
    assert out.run_id == "durable-r1"
    assert out.user_message_id == 101
    assert fake_run_manager.last_start_kwargs["text"] == "交互里有哪些规则？"


@pytest.mark.asyncio
async def test_message_entrypoints_reject_blank_content(fake_svc, fake_run_manager) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(), db, user)

    calls = (
        api.stream_message(
            "s1",
            api.SystemAgentMessageCreate(content="   "),
            db,
            user,
        ),
        api.start_system_agent_run(
            "s1",
            api.SystemAgentRunCreate(
                content=" \n ",
                client_request_id="request-blank-api",
            ),
            db,
            user,
        ),
    )
    for call in calls:
        with pytest.raises(HTTPException) as exc_info:
            await call
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "EMPTY_MESSAGE"

    assert fake_run_manager.last_start_kwargs == {}


@pytest.mark.asyncio
async def test_run_entrypoint_accepts_image_only_message(fake_svc, fake_run_manager) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(), db, user)
    data_url = "data:image/png;base64,iVBORw0KGgo="

    await api.start_system_agent_run(
        "s1",
        api.SystemAgentRunCreate(
            content="",
            client_request_id="request-image-only",
            attachments=[
                    SystemAgentImageAttachment(
                    source="data_url",
                    mime_type="image/png",
                    data_url=data_url,
                    name="paste.png",
                )
            ],
        ),
        db,
        user,
    )

    assert fake_run_manager.last_start_kwargs["text"] == ""
    assert fake_run_manager.last_start_kwargs["attachments"] == [
        {
            "kind": "image",
            "source": "data_url",
            "mime_type": "image/png",
            "data_url": data_url,
            "name": "paste.png",
        }
    ]


@pytest.mark.asyncio
async def test_stop_replace_rejects_blank_content_before_manager(
    monkeypatch,
    fake_run_manager,
) -> None:
    monkeypatch.setattr(api, "_owned_run", AsyncMock(return_value=SimpleNamespace(id="run-1")))
    fake_run_manager.stop_and_replace = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await api.stop_and_replace_system_agent_run(
            "run-1",
            api.SystemAgentStopReplaceCreate(
                client_request_id="request-stop-blank",
                content="   ",
            ),
            AsyncMock(),
            SimpleNamespace(id=7),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "EMPTY_REPLACEMENT"
    fake_run_manager.stop_and_replace.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_input_routes_normalize_and_forward_payloads(
    monkeypatch,
    fake_run_manager,
) -> None:
    owned = AsyncMock(return_value=SimpleNamespace(id="run-1"))
    monkeypatch.setattr(api, "_owned_run", owned)
    db = AsyncMock()
    user = SimpleNamespace(id=7)

    steer = await api.steer_system_agent_run(
        "run-1",
        api.SystemAgentRunInputCreate(
            client_request_id="request-steer-api",
            content="  改用另一个方案  ",
        ),
        db,
        user,
    )
    assert steer.kind == "steer"
    assert fake_run_manager.last_input_kwargs["payload"] == {
        "content": "改用另一个方案"
    }

    await api.steer_system_agent_run(
        "run-1",
        api.SystemAgentRunInputCreate(
            client_request_id="request-steer-image-api",
            attachments=[
                {
                    "kind": "image",
                    "source": "data_url",
                    "data_url": "data:image/png;base64,iVBORw0KGgo=",
                }
            ],
        ),
        db,
        user,
    )
    assert fake_run_manager.last_input_kwargs["payload"] == {
        "content": "",
        "attachments": [
            {
                "kind": "image",
                "source": "data_url",
                "mime_type": "image/png",
                "data_url": "data:image/png;base64,iVBORw0KGgo=",
            }
        ],
    }

    resumed = await api.resume_system_agent_run_with_input(
        "run-1",
        api.SystemAgentRunInputCreate(
            client_request_id="request-input-api",
            fallback_provider_id=9,
        ),
        db,
        user,
    )
    assert resumed.kind == "user_input"
    assert fake_run_manager.last_input_kwargs["payload"] == {
        "content": "",
        "fallback_provider_id": 9,
    }

    approval = await api.approve_system_agent_run(
        "run-1",
        api.SystemAgentRunInputCreate(
            client_request_id="request-approval-api",
            approved=True,
            approved_tools=[" scheduler.list ", ""],
        ),
        db,
        user,
    )
    assert approval.kind == "approval"
    assert fake_run_manager.last_input_kwargs["payload"] == {
        "approved": True,
        "approved_tools": ["scheduler.list"],
        "content": "",
    }
    assert owned.await_count == 4


@pytest.mark.asyncio
async def test_stop_replace_normalizes_and_forwards_payload(
    monkeypatch,
    fake_run_manager,
) -> None:
    monkeypatch.setattr(api, "_owned_run", AsyncMock(return_value=SimpleNamespace(id="run-1")))

    await api.stop_and_replace_system_agent_run(
        "run-1",
        api.SystemAgentStopReplaceCreate(
            client_request_id="request-stop-replace-api",
            content="  新任务  ",
        ),
        AsyncMock(),
        SimpleNamespace(id=7),
    )

    assert fake_run_manager.last_stop_replace_kwargs == {
        "run_id": "run-1",
        "web_user_id": 7,
        "client_request_id": "request-stop-replace-api",
        "text": "新任务",
        "model_selection": None,
    }


@pytest.mark.asyncio
async def test_list_durable_runs_is_scoped_to_current_user(fake_run_manager) -> None:
    user = SimpleNamespace(id=7)

    rows = await api.list_system_agent_runs(
        user,
        status="failed",
        since=None,
        until=None,
        limit=25,
        include_bot=False,
    )

    assert [row.run_id for row in rows] == ["durable-r1"]
    assert fake_run_manager.last_list_kwargs == {
        "web_user_id": 7,
        "status": "failed",
        "since": None,
        "until": None,
        "limit": 25,
        "include_bot": False,
    }


@pytest.mark.asyncio
async def test_queued_run_reports_tasks_ahead_and_queue_item_can_replace(fake_run_manager) -> None:
    """排队中的任务带上 queue_ahead（前面还有几个），执行中的任务不带；排队消息可改为停止并替换。"""

    user = SimpleNamespace(id=7)
    rows = await api.list_system_agent_runs(
        user, status=None, since=None, until=None, limit=20, include_bot=False
    )
    assert rows[0].status == "running" and rows[0].queue_ahead is None

    out = await api.replace_with_system_agent_queue_item("turn-1", user)
    assert fake_run_manager.last_queue_replace_kwargs == {"turn_id": "turn-1", "web_user_id": 7}
    assert out.status == "queued"
    assert out.queue_ahead == 2


@pytest.mark.asyncio
async def test_list_durable_runs_can_filter_by_session(fake_run_manager) -> None:
    user = SimpleNamespace(id=7)

    await api.list_system_agent_runs(
        user,
        status=None,
        since=None,
        until=None,
        limit=20,
        include_bot=True,
        session_id="s1",
    )

    assert fake_run_manager.last_list_kwargs["session_id"] == "s1"
    assert fake_run_manager.last_list_kwargs["limit"] == 20


@pytest.mark.asyncio
async def test_list_sessions_compact_omits_memory(fake_svc) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(title="hi"), db, user)
    for session in fake_svc.sessions.values():
        session.memory_summary = "很长的会话摘要" * 50
        session.memory_state = {"facts": ["a"] * 20}

    full = await api.list_sessions(db, user, status="active", origin=None, include_bot=False, limit=50, compact=False)
    compact = await api.list_sessions(db, user, status="active", origin=None, include_bot=False, limit=50, compact=True)

    assert full[0].memory_summary
    assert full[0].memory_state
    assert compact[0].memory_summary == ""
    assert compact[0].memory_state == {}
    assert compact[0].id == full[0].id
    assert compact[0].title == full[0].title


@pytest.mark.asyncio
async def test_owned_run_releases_read_transaction_before_manager_call() -> None:
    row = SimpleNamespace(id="run-owned", web_user_id=7, channel="web")
    result = SimpleNamespace(scalar_one_or_none=lambda: row)
    db = AsyncMock()
    db.execute.return_value = result

    owned = await api._owned_run(db, row.id, row.web_user_id)

    assert owned is row
    db.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_stream_exception_does_not_expose_raw_error(fake_svc, fake_run_manager) -> None:
    fake_run_manager.broken_stream = True
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(), db, user)

    response = await api.stream_message(
        "s1",
        api.SystemAgentMessageCreate(content="你好"),
        db,
        user,
    )
    chunks = [
        chunk if isinstance(chunk, str) else chunk.decode()
        async for chunk in response.body_iterator
    ]
    body = "".join(chunks)

    assert "sk-sensitive-secret-value" not in body
    assert "RUN_STREAM_FAILED" in body
    assert "RuntimeError" in body


@pytest.mark.asyncio
async def test_retry_failed_message_stream(fake_svc, fake_run_manager) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(), db, user)
    fake_svc.messages["s1"] = [
        SimpleNamespace(id=9, session_id="s1", role="user", run_status="failed")
    ]

    response = await api.retry_message(
        "s1",
        9,
        api.SystemAgentMessageRetry(
            fallback_provider_id=12,
            approved_tools=["scheduler.list"],
        ),
        db,
        user,
    )
    chunks = [
        chunk if isinstance(chunk, str) else chunk.decode()
        async for chunk in response.body_iterator
    ]
    events = [json.loads(line) for line in "".join(chunks).splitlines() if line]

    assert events[-1]["type"] == "done"
    assert events[-1]["ok"] is True
    assert fake_run_manager.last_start_kwargs["fallback_provider_id"] == 12
    assert fake_run_manager.last_start_kwargs["approved_tools"] == ["scheduler.list"]


@pytest.mark.asyncio
async def test_retry_rejects_non_failed_message(fake_svc) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(), db, user)
    fake_svc.messages["s1"] = [
        SimpleNamespace(id=10, session_id="s1", role="user", run_status="succeeded")
    ]

    with pytest.raises(HTTPException) as exc_info:
        await api.retry_message(
            "s1",
            10,
            api.SystemAgentMessageRetry(),
            db,
            user,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_regenerate_run_passes_original_pair_ids(fake_svc, fake_run_manager) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(), db, user)
    fake_svc.messages["s1"] = [
        SimpleNamespace(id=21, session_id="s1", role="user", run_status="succeeded"),
        SimpleNamespace(id=22, session_id="s1", role="assistant", run_status="completed"),
    ]

    out = await api.start_system_agent_regenerate_run(
        "s1",
        21,
        api.SystemAgentRegenerateRunCreate(
            assistant_message_id=22,
            content="编辑后的问题",
            client_request_id="request-regenerate-api",
        ),
        db,
        user,
    )

    assert out.kind == "regenerate"
    assert out.user_message_id == 21
    assert fake_run_manager.last_start_kwargs["text"] == "编辑后的问题"
    assert fake_run_manager.last_start_kwargs["regenerate_message_id"] == 21
    assert fake_run_manager.last_start_kwargs["regenerate_assistant_message_id"] == 22


@pytest.mark.asyncio
async def test_regenerate_rejects_old_pair_before_updating_account(
    fake_svc,
    fake_run_manager,
) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    session = await api.create_session(api.SystemAgentSessionCreate(), db, user)
    fake_svc.messages["s1"] = [
        SimpleNamespace(id=21, session_id="s1", role="user", run_status="succeeded"),
        SimpleNamespace(id=22, session_id="s1", role="assistant", run_status="completed"),
        SimpleNamespace(id=23, session_id="s1", role="user", run_status="succeeded"),
        SimpleNamespace(id=24, session_id="s1", role="assistant", run_status="completed"),
    ]

    with pytest.raises(HTTPException) as exc_info:
        await api.start_system_agent_regenerate_run(
            "s1",
            21,
            api.SystemAgentRegenerateRunCreate(
                assistant_message_id=22,
                account_id=3,
                client_request_id="request-regenerate-old",
            ),
            db,
            user,
        )

    assert exc_info.value.status_code == 409
    assert session.account_id is None
    assert fake_run_manager.last_start_kwargs == {}


@pytest.mark.asyncio
async def test_secret_input_locks_action_before_writing(monkeypatch) -> None:
    row = SimpleNamespace(
        id="act-secret",
        actor_user_id=7,
        tool_name="providers.save",
        status="pending",
        expires_at=None,
        secret_payload_enc=None,
        secret_fields=None,
        arguments={},
        error_code="API_KEY_REQUIRED",
        error_message="缺少 Key",
    )
    lock_mock = AsyncMock(return_value=row)
    monkeypatch.setattr(api, "lock_action", lock_mock)
    monkeypatch.setattr(
        api,
        "get_registry",
        lambda: SimpleNamespace(
            get=lambda _name: SimpleNamespace(secret_argument_names=("api_key",))
        ),
    )
    monkeypatch.setattr(api, "encrypt_secret_payload", lambda _data: "encrypted")
    db = AsyncMock()

    out = await api.secret_input_action(
        "act-secret",
        api.SystemAgentSecretInput(fields={"api_key": "sk-new-secret-value"}),
        db,
        SimpleNamespace(id=7),
    )

    lock_mock.assert_awaited_once_with(db, "act-secret")
    assert out.has_secret is True
    assert row.secret_payload_enc == "encrypted"
    assert row.error_code is None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_secret_input_rejects_replacing_verified_probe_key(monkeypatch) -> None:
    row = SimpleNamespace(
        id="act-probe",
        actor_user_id=7,
        tool_name="providers.probe_and_add",
        status="pending",
        expires_at=None,
        secret_payload_enc="encrypted-verified-key",
        secret_fields=["api_key"],
        arguments={"has_api_key": True},
        error_code=None,
        error_message=None,
    )
    monkeypatch.setattr(api, "lock_action", AsyncMock(return_value=row))
    monkeypatch.setattr(
        api,
        "get_registry",
        lambda: SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                secret_argument_names=("api_key",),
                allow_secret_input=False,
            )
        ),
    )
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await api.secret_input_action(
            "act-probe",
            api.SystemAgentSecretInput(fields={"api_key": "sk-unverified-replacement"}),
            db,
            SimpleNamespace(id=7),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert row.secret_payload_enc == "encrypted-verified-key"
    db.commit.assert_not_awaited()


# ─────────── 密钥占位符还原：Web 历史边界 ───────────


@pytest.mark.asyncio
async def test_list_messages_restores_placeholders_for_owner(fake_svc) -> None:
    """Web 历史是面向 owner 的显示边界：落库占位符在这里还原成真实值。"""
    from app.services.system_agent.secrets import build_secret_map, encrypt_secret_map

    secret = "sk-web-history-secret-value-123"
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    session = SimpleNamespace(
        id="s-restore",
        web_user_id=7,
        channel="web",
        secret_map_enc=encrypt_secret_map(build_secret_map([secret])),
    )
    fake_svc.sessions[session.id] = session
    fake_svc.messages[session.id] = [
        # 落库内容保持占位符：映射里的真实值只存在于 secret_map_enc
        SimpleNamespace(
            id=11,
            session_id=session.id,
            role="user",
            content={"text": "key 是 [[TP-CRED-1]]"},
            usage=None,
            run_status="completed",
            error_code=None,
            error_message=None,
            retry_count=0,
            created_at=None,
        ),
        SimpleNamespace(
            id=12,
            session_id=session.id,
            role="assistant",
            content={"text": "好的", "reasoning": "看看 [[TP-CRED-1]] 是什么"},
            usage=None,
            run_status="completed",
            error_code=None,
            error_message=None,
            retry_count=0,
            created_at=None,
        ),
    ]

    rows = await api.list_messages(session.id, db, user, limit=100, before_id=None)

    assert secret not in str(fake_svc.messages[session.id][0].content)
    assert rows[0].content["text"] == f"key 是 {secret}"
    # reasoning 字段同样在显示边界还原
    assert rows[1].content["reasoning"] == f"看看 {secret} 是什么"
    assert rows[1].content["text"] == "好的"


@pytest.mark.asyncio
async def test_list_messages_without_mapping_keeps_placeholder(fake_svc) -> None:
    """老会话 / 映射不可用时历史保持占位符，不猜值也不丢内容。"""
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    session = SimpleNamespace(
        id="s-no-map",
        web_user_id=7,
        channel="web",
        secret_map_enc=None,
    )
    fake_svc.sessions[session.id] = session
    fake_svc.messages[session.id] = [
        SimpleNamespace(
            id=21,
            session_id=session.id,
            role="user",
            content={"text": "key 是 [[TP-CRED-1]]"},
            usage=None,
            run_status="completed",
            error_code=None,
            error_message=None,
            retry_count=0,
            created_at=None,
        )
    ]

    rows = await api.list_messages(session.id, db, user, limit=100, before_id=None)

    assert rows[0].content == {"text": "key 是 [[TP-CRED-1]]"}


@pytest.mark.asyncio
async def test_list_messages_preserves_attachment_payloads(fake_svc) -> None:
    """还原只处理 text/reasoning，附件等结构必须原样返回。"""
    from app.services.system_agent.secrets import build_secret_map, encrypt_secret_map

    db = AsyncMock()
    user = SimpleNamespace(id=7)
    session = SimpleNamespace(
        id="s-attach",
        web_user_id=7,
        channel="web",
        secret_map_enc=encrypt_secret_map(build_secret_map(["sk-attach-secret-value-123"])),
    )
    fake_svc.sessions[session.id] = session
    attachments = [{"type": "image", "url": "/api/x", "mime_type": "image/png"}]
    fake_svc.messages[session.id] = [
        SimpleNamespace(
            id=31,
            session_id=session.id,
            role="user",
            content={"text": "看这张图 [[TP-CRED-1]]", "attachments": attachments},
            usage=None,
            run_status="completed",
            error_code=None,
            error_message=None,
            retry_count=0,
            created_at=None,
        )
    ]

    rows = await api.list_messages(session.id, db, user, limit=100, before_id=None)

    assert rows[0].content["attachments"] == attachments
    assert "sk-attach-secret-value-123" in rows[0].content["text"]


# ─────────── 密钥占位符还原：Web 实时流边界 ───────────


class _FakeSecretRunManager:
    """`stream_system_agent_run` 用的最小 run manager 替身。"""

    def __init__(self, events) -> None:  # noqa: ANN001
        self.events = events

    async def stream_events(self, run_id, *, after_seq=0):
        for event in self.events:
            if int(event.get("seq") or 0) > after_seq:
                yield event


class _FakeSecretRunDB:
    """按 run_id → session 读占位符映射的最小 DB 替身。"""

    def __init__(self, session_row) -> None:  # noqa: ANN001
        self.session_row = session_row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, model, pk):  # noqa: ANN001
        name = getattr(model, "__name__", "")
        if name == "SystemAgentRun":
            return SimpleNamespace(id="run-restore", session_id=self.session_row.id)
        if name == "SystemAgentSession":
            return self.session_row
        return None


def _restore_session(secret: str):
    from app.services.system_agent.secrets import build_secret_map, encrypt_secret_map

    return SimpleNamespace(
        id="sess-sse",
        web_user_id=7,
        channel="web",
        secret_map_enc=encrypt_secret_map(build_secret_map([secret])),
    )


@pytest.mark.asyncio
async def test_run_stream_restores_placeholders_for_owner(monkeypatch) -> None:
    """Web 实时流是面向 owner 的显示边界：assistant_message 的内容先还原再下发。"""
    secret = "sk-web-sse-secret-value-123"
    events = [
        {"type": "run_started", "run_id": "run-restore", "session_id": "sess-sse", "seq": 1},
        {
            "type": "assistant_message",
            "content": "我会用 [[TP-CRED-1]] 注册。",
            "reasoning": "用户给了 [[TP-CRED-1]]",
            "run_id": "run-restore",
            "session_id": "sess-sse",
            "seq": 2,
        },
        {"type": "done", "ok": True, "run_id": "run-restore", "session_id": "sess-sse", "seq": 3},
    ]
    session_row = _restore_session(secret)
    monkeypatch.setattr(
        api,
        "get_system_agent_run_manager",
        lambda: _FakeSecretRunManager(events),
    )
    monkeypatch.setattr(
        api,
        "AsyncSessionLocal",
        lambda: _FakeSecretRunDB(session_row),
    )
    monkeypatch.setattr(api, "_owned_run", AsyncMock(return_value=session_row))

    response = await api.stream_system_agent_run(
        "run-restore", AsyncMock(), SimpleNamespace(id=7), after_seq=0
    )
    chunks = [
        chunk if isinstance(chunk, str) else chunk.decode()
        async for chunk in response.body_iterator
    ]
    parsed = [json.loads(line) for line in "".join(chunks).splitlines() if line.strip()]

    assistant = next(event for event in parsed if event["type"] == "assistant_message")
    assert assistant["content"] == f"我会用 {secret} 注册。"
    assert assistant["reasoning"] == f"用户给了 {secret}"
    # 事件源（run 事件库）保持占位符不变，还原只发生在出站流
    assert events[1]["content"] == "我会用 [[TP-CRED-1]] 注册。"


@pytest.mark.asyncio
async def test_run_stream_keeps_placeholder_without_mapping(monkeypatch) -> None:
    """映射缺失时不还原，出站仍是占位符（不猜值）。"""
    events = [
        {
            "type": "assistant_message",
            "content": "我会用 [[TP-CRED-1]] 注册。",
            "run_id": "run-restore",
            "session_id": "sess-sse",
            "seq": 2,
        },
        {"type": "done", "ok": True, "run_id": "run-restore", "session_id": "sess-sse", "seq": 3},
    ]
    monkeypatch.setattr(
        api,
        "get_system_agent_run_manager",
        lambda: _FakeSecretRunManager(events),
    )
    monkeypatch.setattr(
        api,
        "AsyncSessionLocal",
        lambda: _FakeSecretRunDB(SimpleNamespace(id="sess-sse", secret_map_enc=None)),
    )
    monkeypatch.setattr(api, "_owned_run", AsyncMock(return_value=SimpleNamespace(id="run-restore")))

    response = await api.stream_system_agent_run(
        "run-restore", AsyncMock(), SimpleNamespace(id=7), after_seq=0
    )
    chunks = [
        chunk if isinstance(chunk, str) else chunk.decode()
        async for chunk in response.body_iterator
    ]
    parsed = [json.loads(line) for line in "".join(chunks).splitlines() if line.strip()]

    assistant = next(event for event in parsed if event["type"] == "assistant_message")
    assert assistant["content"] == "我会用 [[TP-CRED-1]] 注册。"


@pytest.mark.asyncio
async def test_run_stream_survives_mapping_lookup_failure(monkeypatch) -> None:
    """读映射失败不能打断实时流：降级为不还原，事件照常下发。"""
    events = [
        {
            "type": "assistant_message",
            "content": "我会用 [[TP-CRED-1]] 注册。",
            "run_id": "run-restore",
            "session_id": "sess-sse",
            "seq": 2,
        },
        {"type": "done", "ok": True, "run_id": "run-restore", "session_id": "sess-sse", "seq": 3},
    ]
    monkeypatch.setattr(
        api,
        "get_system_agent_run_manager",
        lambda: _FakeSecretRunManager(events),
    )

    class _BrokenDB:
        async def __aenter__(self):
            raise RuntimeError("db unavailable")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(api, "AsyncSessionLocal", lambda: _BrokenDB())
    monkeypatch.setattr(api, "_owned_run", AsyncMock(return_value=SimpleNamespace(id="run-restore")))

    response = await api.stream_system_agent_run(
        "run-restore", AsyncMock(), SimpleNamespace(id=7), after_seq=0
    )
    chunks = [
        chunk if isinstance(chunk, str) else chunk.decode()
        async for chunk in response.body_iterator
    ]
    parsed = [json.loads(line) for line in "".join(chunks).splitlines() if line.strip()]

    assert any(event["type"] == "done" for event in parsed)
    assistant = next(event for event in parsed if event["type"] == "assistant_message")
    assert assistant["content"] == "我会用 [[TP-CRED-1]] 注册。"


# ─────────── account_id 不存在 → 404 结构化错误（非 FK 500） ───────────


@pytest.mark.asyncio
async def test_create_session_with_missing_account_maps_to_404(fake_svc, monkeypatch) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)

    async def raising_create(_db, **_kwargs):
        raise LookupError("account 404 不存在")

    monkeypatch.setattr(fake_svc, "create_session", raising_create)
    with pytest.raises(HTTPException) as exc_info:
        await api.create_session(api.SystemAgentSessionCreate(account_id=404), db, user)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_update_session_with_missing_account_maps_to_404(fake_svc, monkeypatch) -> None:
    db = AsyncMock()
    user = SimpleNamespace(id=7)
    await api.create_session(api.SystemAgentSessionCreate(account_id=None), db, user)

    async def raising_update(_db, _session, **_kwargs):
        raise LookupError("account 404 不存在")

    monkeypatch.setattr(fake_svc, "update_session", raising_update)
    with pytest.raises(HTTPException) as exc_info:
        await api.update_session(
            "s1",
            api.SystemAgentSessionUpdate(account_id=404),
            db,
            user,
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "ACCOUNT_NOT_FOUND"
