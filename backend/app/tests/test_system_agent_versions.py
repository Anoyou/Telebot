"""System Agent 运行时/依赖版本工具：system.get_versions。"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import gateway_runtime, runtime_versions
from app.services.gateway_runtime import GatewayRuntimeStatus
from app.services.system_agent.context import ToolContext
from app.services.system_agent.registry import get_registry, reset_registry_for_tests
from app.services.system_agent.skills.catalog import BUILTIN_SKILLS
from app.services.system_agent.skills.registry import SkillRegistry
from app.services.system_agent.tool_routing import route_locally, select_tool_specs, tool_domain
from app.services.system_agent.tools import system as system_tools

USER_QUESTION = "现在项目里内置的telethon版本号和gateway版本号各是什么？"


class _FakeManager:
    def __init__(self, status: GatewayRuntimeStatus, binary: str) -> None:
        self._status = status
        self.binary = binary

    def status(self) -> GatewayRuntimeStatus:
        return self._status


class _FakeRedis:
    async def info(self, section: str) -> dict:
        assert section == "server"
        return {
            "redis_version": "7.2.5",
            "redis_mode": "standalone",
            "executable": "/usr/local/bin/redis-server",
            "config_file": "/etc/redis/secret.conf",
            "run_id": "deadbeef",
        }


@pytest.fixture
async def sqlite_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    runtime_versions._GATEWAY_PROBE_CACHE.clear()
    yield
    runtime_versions._GATEWAY_PROBE_CACHE.clear()


def _status(**overrides) -> GatewayRuntimeStatus:
    base = {"state": "not_required", "required": False, "revision": 0, "provider_count": 0}
    base.update(overrides)
    return GatewayRuntimeStatus(**base)


def test_lookup_package_is_case_and_separator_insensitive() -> None:
    exact = runtime_versions.lookup_package("TELETHON")
    assert exact["found"] is True
    assert exact["version"] == runtime_versions.package_version("Telethon")

    normalized = runtime_versions.lookup_package("pydantic_settings")
    assert normalized["found"] is True
    assert normalized["version"]


def test_lookup_package_rejects_invalid_and_suggests_candidates() -> None:
    invalid = runtime_versions.lookup_package("../etc/passwd")
    assert invalid["found"] is False
    assert invalid["error"] == "invalid_package_name"

    fuzzy = runtime_versions.lookup_package("pydant")
    assert fuzzy["found"] is False
    assert any(item["name"].lower().startswith("pydantic") for item in fuzzy["candidates"])


def test_key_packages_and_telethon_details_read_installed_metadata() -> None:
    packages = runtime_versions.collect_key_packages()
    assert packages["telethon"] == runtime_versions.package_version("Telethon")
    assert packages["fastapi"] and packages["sqlalchemy"] and packages["pydantic"]

    telethon = runtime_versions.collect_telethon_details()
    import telethon as telethon_module

    assert telethon["version"] == packages["telethon"]
    assert telethon["module_version"] == telethon_module.__version__
    assert isinstance(telethon["layer"], int) and telethon["layer"] > 0


async def test_gateway_uses_running_process_version(monkeypatch, tmp_path) -> None:
    status = _status(
        state="ready",
        required=True,
        provider_count=1,
        revision=3,
        version="0.1.0-beta.3",
        protocol_version="2",
        build_commit="ABCDEF1234567",
        upstream_commit="ffdb9c9fbc78a6235d59c9ccbdc4243ba35ecdcd",
        codex_client_version="0.50.0",
    )
    monkeypatch.setattr(
        gateway_runtime,
        "gateway_runtime_manager",
        _FakeManager(status, str(tmp_path / "missing")),
    )

    async def boom(_binary):  # pragma: no cover - 不应被调用
        raise AssertionError("运行中不应再探测二进制")

    monkeypatch.setattr(runtime_versions, "probe_gateway_binary_version", boom)
    info = await runtime_versions.collect_gateway_info()
    assert info["source"] == "running_process"
    assert info["running"] is True
    assert info["version"] == "0.1.0-beta.3"
    assert info["build_commit"] == "abcdef1234567"
    assert info["expected_protocol_version"] == gateway_runtime.GATEWAY_PROTOCOL_VERSION


async def test_gateway_reports_unavailable_without_binary(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        gateway_runtime,
        "gateway_runtime_manager",
        _FakeManager(_status(), str(tmp_path / "telepilot-gateway")),
    )
    info = await runtime_versions.collect_gateway_info()
    assert info["source"] == "unavailable"
    assert info["version"] is None
    assert info["binary_present"] is False


def _write_fake_gateway(path: Path, counter: Path) -> None:
    script = f'''#!{sys.executable}
import http.server, json, socketserver, sys
sock = sys.argv[sys.argv.index("-socket") + 1]
with open({str(counter)!r}, "a") as fh:
    fh.write("x")
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/version":
            self.send_response(404); self.end_headers(); return
        body = json.dumps({{
            "version": "9.9.9-test",
            "gateway_protocol_version": "2",
            "upstream_commit": "ffdb9c9fbc78a6235d59c9ccbdc4243ba35ecdcd",
            "build_commit": "1234567890ab",
            "codex_contract_review_date": "2026-08-04",
        }}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def address_string(self):
        return "uds"
    def log_message(self, *args):
        pass
class S(socketserver.UnixStreamServer):
    def get_request(self):
        request, _ = super().get_request()
        return request, ("uds", 0)
S(sock, H).serve_forever()
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


async def test_gateway_probes_builtin_binary_when_not_running(monkeypatch, tmp_path) -> None:
    binary = tmp_path / "telepilot-gateway"
    counter = tmp_path / "spawn-count"
    _write_fake_gateway(binary, counter)
    monkeypatch.setattr(
        gateway_runtime,
        "gateway_runtime_manager",
        _FakeManager(_status(), str(binary)),
    )

    info = await runtime_versions.collect_gateway_info()
    assert info["source"] == "binary_probe", info
    assert info["running"] is False
    assert info["version"] == "9.9.9-test"
    assert info["protocol_version"] == "2"
    assert info["build_commit"] == "1234567890ab"

    # 同一二进制第二次查询命中缓存，不再拉起子进程。
    again = await runtime_versions.collect_gateway_info()
    assert again["version"] == "9.9.9-test"
    assert counter.read_text() == "x"

    skipped = await runtime_versions.collect_gateway_info(probe_binary=False)
    assert skipped["source"] == "not_probed"
    assert skipped["version"] is None


async def test_redis_info_only_returns_version_fields(monkeypatch) -> None:
    import app.redis_client as redis_client

    monkeypatch.setattr(redis_client, "get_redis", lambda: _FakeRedis())
    info = await runtime_versions.collect_redis_info()
    assert info == {"server_version": "7.2.5", "mode": "standalone"}


async def test_database_info_reads_sqlite_version(sqlite_session) -> None:
    info = await runtime_versions.collect_database_info(sqlite_session)
    assert info["dialect"] == "sqlite"
    assert info["server_version"]
    assert info["migration_revision"] is None


async def test_get_versions_tool_shape_and_no_secret_leak(
    monkeypatch, tmp_path, sqlite_session
) -> None:
    import app.redis_client as redis_client

    secret = "sk-super-secret-value-should-never-appear"
    monkeypatch.setenv("TELEPILOT_TEST_SECRET", secret)
    monkeypatch.setenv("MASTER_KEY", secret)
    monkeypatch.setattr(redis_client, "get_redis", lambda: _FakeRedis())
    monkeypatch.setattr(
        gateway_runtime,
        "gateway_runtime_manager",
        _FakeManager(
            _status(state="ready", required=True, provider_count=1, version="0.1.0-beta.3"),
            str(tmp_path / "missing"),
        ),
    )

    ctx = ToolContext(db=sqlite_session, channel="web", role="viewer", account_id=None)
    result = await system_tools.get_versions(ctx, {"package": "telethon"})

    from app import __version__

    assert result["business_changed"] is False
    assert result["app"]["version"] == __version__
    assert result["python"]["version"] == ".".join(str(part) for part in sys.version_info[:3])
    assert result["telethon"]["version"] == runtime_versions.package_version("Telethon")
    assert result["gateway"]["version"] == "0.1.0-beta.3"
    assert result["packages"]["telethon"] == result["telethon"]["version"]
    assert result["package_lookup"]["found"] is True
    assert result["database"]["dialect"] == "sqlite"
    assert result["redis"]["server_version"] == "7.2.5"
    assert result["workers"]["shares_backend_environment"] is True
    assert "platform" in result

    dumped = json.dumps(result, ensure_ascii=False, default=str)
    assert secret not in dumped
    assert "secret.conf" not in dumped
    for key, value in os.environ.items():
        if key.isupper() and len(value) >= 16 and ("KEY" in key or "SECRET" in key or "TOKEN" in key):
            assert value not in dumped


async def test_get_versions_tool_failure_is_contained(monkeypatch) -> None:
    async def boom(*_args, **_kwargs):
        raise RuntimeError("probe exploded with sk-should-not-leak")

    monkeypatch.setattr(runtime_versions, "collect_runtime_versions", boom)
    ctx = ToolContext(db=SimpleNamespace(), channel="web", role="viewer", account_id=None)
    result = await system_tools.get_versions(ctx, {})
    assert result["error"] == "version_probe_failed"
    assert result["business_changed"] is False
    assert "sk-should-not-leak" not in result["message"]


def test_get_versions_registered_read_only_for_viewer_web_and_bot() -> None:
    reset_registry_for_tests()
    spec = get_registry().get("system.get_versions")
    assert spec is not None
    assert spec.read_only is True
    assert spec.min_role == "viewer"
    assert set(spec.channels) == {"web", "bot"}
    assert "Telethon" in spec.description and "Gateway" in spec.description
    assert spec.input_schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "text",
    [
        USER_QUESTION,
        "telethon 现在是什么版本",
        "后端依赖库版本列一下",
        "Python 版本和数据库版本是多少",
        "what is the telethon runtime version",
    ],
)
def test_version_questions_route_to_get_versions(text: str) -> None:
    reset_registry_for_tests()
    specs = get_registry().list_all()
    available = {tool_domain(spec) for spec in specs}
    route = route_locally(text, available=available)

    assert route is not None
    assert "system" in route.domains
    skills = SkillRegistry(BUILTIN_SKILLS)
    selected = skills.select(route)
    narrowed = skills.narrow_tools(select_tool_specs(specs, route), selected)
    assert "system.get_versions" in {spec.name for spec in narrowed}
