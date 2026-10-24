"""运行时与依赖版本只读采集。

供 System Agent ``system.get_versions`` 等只读诊断入口复用，回答
“当前内置的 Telethon / Gateway / Python / 数据库版本是什么”这类问题。

安全约束：
- 只返回版本号、提交哈希、平台标识等公开元数据；
- 绝不读取或返回环境变量值、连接串、密钥、主机名与文件内容；
- 每项探测相互独立，任一失败只在对应字段写入简短错误，不影响其它字段。
"""

from __future__ import annotations

import asyncio
import importlib.metadata as importlib_metadata
import os
import platform
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from .redactor import redact_text

# 后端核心依赖：按“用户最常问 → 基础设施”排序。键为展示名，值为发行包名。
KEY_PACKAGES: tuple[tuple[str, str], ...] = (
    ("telethon", "Telethon"),
    ("cryptg", "cryptg"),
    ("python-socks", "python-socks"),
    ("fastapi", "fastapi"),
    ("starlette", "starlette"),
    ("uvicorn", "uvicorn"),
    ("pydantic", "pydantic"),
    ("pydantic-settings", "pydantic-settings"),
    ("sqlalchemy", "SQLAlchemy"),
    ("alembic", "alembic"),
    ("asyncpg", "asyncpg"),
    ("psycopg2-binary", "psycopg2-binary"),
    ("aiosqlite", "aiosqlite"),
    ("redis", "redis"),
    ("httpx", "httpx"),
    ("aiohttp", "aiohttp"),
    ("anyio", "anyio"),
    ("cryptography", "cryptography"),
    ("pillow", "Pillow"),
    ("jsonschema", "jsonschema"),
)

_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_GATEWAY_PROBE_TIMEOUT_SECONDS = 4.0
_GATEWAY_PROBE_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_GATEWAY_PROBE_LOCK = asyncio.Lock()


def _short_error(exc: BaseException) -> str:
    return redact_text(f"{type(exc).__name__}: {exc}")[:200]


def normalize_package_name(name: str) -> str:
    """PEP 503 规范化：大小写与 ``-_.`` 不敏感。"""

    return re.sub(r"[-_.]+", "-", str(name or "").strip()).lower()


def package_version(dist_name: str) -> str | None:
    try:
        return importlib_metadata.version(dist_name)
    except importlib_metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001
        return None


def collect_key_packages() -> dict[str, str | None]:
    return {label: package_version(dist) for label, dist in KEY_PACKAGES}


def lookup_package(query: str, *, limit: int = 10) -> dict[str, Any]:
    """按名称查询任意已安装 Python 包；精确未命中时返回包含关系的候选。"""

    raw = str(query or "").strip()
    if not _PACKAGE_NAME_RE.fullmatch(raw):
        return {
            "query": raw[:100],
            "found": False,
            "error": "invalid_package_name",
            "message": "包名只能包含字母、数字、点、下划线和连字符。",
        }
    wanted = normalize_package_name(raw)
    exact = package_version(raw)
    if exact is not None:
        return {"query": raw, "found": True, "name": raw, "version": exact}
    matches: dict[str, str] = {}
    try:
        for dist in importlib_metadata.distributions():
            name = str(dist.metadata.get("Name") or "").strip()
            if not name:
                continue
            normalized = normalize_package_name(name)
            if normalized == wanted:
                return {"query": raw, "found": True, "name": name, "version": dist.version}
            if wanted in normalized and name not in matches:
                matches[name] = str(dist.version)
    except Exception as exc:  # noqa: BLE001
        return {"query": raw, "found": False, "error": _short_error(exc)}
    candidates = [
        {"name": name, "version": version}
        for name, version in sorted(matches.items(), key=lambda item: item[0].lower())[:limit]
    ]
    return {"query": raw, "found": False, "candidates": candidates}


def collect_telethon_details() -> dict[str, Any]:
    """Telethon 包版本 + 运行时模块版本 + MTProto Layer。"""

    details: dict[str, Any] = {
        "version": package_version("Telethon"),
        "module_version": None,
        "layer": None,
        "cryptg": package_version("cryptg"),
    }
    try:
        from telethon import version as telethon_version

        details["module_version"] = str(getattr(telethon_version, "__version__", "") or "") or None
    except Exception as exc:  # noqa: BLE001
        details["error"] = _short_error(exc)
        return details
    try:
        from telethon.tl.alltlobjects import LAYER

        details["layer"] = int(LAYER)
    except Exception:  # noqa: BLE001
        pass
    return details


def collect_python_info() -> dict[str, Any]:
    return {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "compiler": platform.python_compiler(),
    }


def _os_pretty_name() -> str | None:
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')[:120] or None
    return None


def _in_container() -> bool:
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8")
    except OSError:
        return False
    return any(token in cgroup for token in ("docker", "containerd", "kubepods", "libpod"))


def collect_platform_info() -> dict[str, Any]:
    libc_name, libc_version = ("", "")
    try:
        libc_name, libc_version = platform.libc_ver()
    except Exception:  # noqa: BLE001
        pass
    return {
        "system": platform.system(),
        "kernel_release": platform.release(),
        "machine": platform.machine(),
        "os": _os_pretty_name(),
        "libc": f"{libc_name} {libc_version}".strip() or None,
        "in_container": _in_container(),
    }


def collect_app_info() -> dict[str, Any]:
    from .. import APP_STAGE, __version__

    info: dict[str, Any] = {
        "version": __version__,
        "stage": APP_STAGE,
        "revision": None,
        "revision_short": None,
        "channel": None,
    }
    try:
        from ..api.system_health import _release_channel, _runtime_revision

        revision = _runtime_revision()
        info["revision"] = revision
        info["revision_short"] = revision[:12] if revision else None
        info["channel"] = _release_channel(revision)
    except Exception as exc:  # noqa: BLE001
        info["revision_error"] = _short_error(exc)
    return info


def _same_executable(pid: int | None) -> bool | None:
    if not pid:
        return None
    try:
        worker_exe = os.path.realpath(f"/proc/{int(pid)}/exe")
        return worker_exe == os.path.realpath(sys.executable)
    except OSError:
        return None


def collect_worker_runtime() -> dict[str, Any]:
    """Worker 由 web 主进程的 supervisor 以 multiprocessing spawn 拉起。

    子进程与主进程共享同一 Python 解释器与 site-packages，因此 Telethon 等
    依赖版本与本工具读取到的值一致；这里额外用 ``/proc/<pid>/exe`` 校验。
    """

    info: dict[str, Any] = {
        "process_model": "web 主进程 supervisor 以 multiprocessing spawn 拉起的子进程（同一容器、同一 Python 环境）",
        "shares_backend_environment": True,
        "total": 0,
        "alive": 0,
        "same_executable_checked": 0,
        "same_executable_mismatch": 0,
    }
    try:
        from ..worker.supervisor import get_worker_runtime_snapshot

        rows = get_worker_runtime_snapshot()
    except Exception as exc:  # noqa: BLE001
        info["error"] = _short_error(exc)
        return info
    info["total"] = len(rows)
    for row in rows:
        if not row.get("alive"):
            continue
        info["alive"] += 1
        same = _same_executable(row.get("pid"))  # type: ignore[arg-type]
        if same is None:
            continue
        info["same_executable_checked"] += 1
        if not same:
            info["same_executable_mismatch"] += 1
    if info["same_executable_mismatch"]:
        info["shares_backend_environment"] = False
        info["warning"] = "部分 Worker 子进程的 Python 可执行文件与主进程不同，依赖版本可能不一致，建议重启 Worker。"
    return info


async def _probe_gateway_binary(binary: Path) -> dict[str, Any]:
    """短暂启动内置 Gateway 二进制，只读取其 ``GET /version`` 后立即退出。

    不下发任何 Provider 快照，也不复用正式 socket，因此不会影响运行中的 Gateway。
    """

    tmpdir = tempfile.mkdtemp(prefix="tpgwv-")
    socket_path = os.path.join(tmpdir, "v.sock")
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            str(binary),
            "-socket",
            socket_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _GATEWAY_PROBE_TIMEOUT_SECONDS
        last_error: BaseException | None = None
        transport = httpx.AsyncHTTPTransport(uds=socket_path)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gateway", timeout=2.0
        ) as client:
            while loop.time() < deadline:
                if process.returncode is not None:
                    raise RuntimeError(f"Gateway 版本探测进程提前退出（code={process.returncode}）")
                try:
                    response = await client.get("/version")
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise RuntimeError("Gateway /version 返回的不是 JSON 对象")
                    return payload
                except (httpx.TransportError, OSError) as exc:
                    last_error = exc
                    await asyncio.sleep(0.05)
        raise TimeoutError(f"Gateway 版本探测超时：{last_error}")
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        shutil.rmtree(tmpdir, ignore_errors=True)


async def probe_gateway_binary_version(binary: Path) -> dict[str, Any]:
    """按二进制 (路径, mtime, size) 缓存探测结果，避免重复拉起子进程。"""

    stat = binary.stat()
    key = (str(binary), int(stat.st_mtime_ns), int(stat.st_size))
    cached = _GATEWAY_PROBE_CACHE.get(key)
    if cached is not None:
        return dict(cached)
    async with _GATEWAY_PROBE_LOCK:
        cached = _GATEWAY_PROBE_CACHE.get(key)
        if cached is not None:
            return dict(cached)
        payload = await _probe_gateway_binary(binary)
        _GATEWAY_PROBE_CACHE.clear()
        _GATEWAY_PROBE_CACHE[key] = dict(payload)
        return dict(payload)


def _clean_commit(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text == "dev" or _COMMIT_RE.fullmatch(text):
        return text.lower()
    return text[:64]


async def collect_gateway_info(*, probe_binary: bool = True) -> dict[str, Any]:
    """内置 telepilot-gateway（Go 实现的 Codex Responses 网关）版本。

    优先读取运行中进程启动时 ``GET /version`` 的结果；Gateway 按需启动，未运行时
    （没有 codex_gateway 执行后端的 Provider）再短暂探测镜像内置二进制。
    """

    from .gateway_runtime import GATEWAY_PROTOCOL_VERSION, gateway_runtime_manager

    status = gateway_runtime_manager.status()
    binary = Path(gateway_runtime_manager.binary)
    binary_present = binary.is_file() and os.access(binary, os.X_OK)
    info: dict[str, Any] = {
        "component": "telepilot-gateway：内置 Go 网关子进程，为执行后端为 codex_gateway 的 Provider 提供 Codex Responses 兼容转发",
        "state": status.state,
        "running": bool(status.version) and status.state in {"ready", "starting"},
        "required": status.required,
        "provider_count": status.provider_count,
        "version": status.version,
        "protocol_version": status.protocol_version,
        "expected_protocol_version": GATEWAY_PROTOCOL_VERSION,
        "build_commit": _clean_commit(status.build_commit),
        "upstream_commit": _clean_commit(status.upstream_commit),
        "contract_review_date": status.contract_review_date,
        "codex_client_version": status.codex_client_version,
        "codex_version_source": status.codex_version_source,
        "binary_present": binary_present,
        "source": "running_process" if status.version else None,
    }
    if status.error:
        info["error"] = redact_text(status.error)[:200]
    if status.version:
        return info
    if not binary_present:
        info["source"] = "unavailable"
        info["message"] = "当前运行环境没有内置 Gateway 二进制（源码开发模式常见），无法读取运行时版本。"
        return info
    if not probe_binary:
        info["source"] = "not_probed"
        info["message"] = "Gateway 当前未运行；未启用二进制探测。"
        return info
    try:
        payload = await probe_gateway_binary_version(binary)
    except Exception as exc:  # noqa: BLE001
        info["source"] = "probe_failed"
        info["probe_error"] = _short_error(exc)
        return info
    info.update(
        {
            "source": "binary_probe",
            "version": str(payload.get("version") or "") or None,
            "protocol_version": str(payload.get("gateway_protocol_version") or "") or None,
            "build_commit": _clean_commit(payload.get("build_commit")),
            "upstream_commit": _clean_commit(payload.get("upstream_commit")),
            "contract_review_date": str(payload.get("codex_contract_review_date") or "") or None,
            "message": "Gateway 按需启动，当前未运行；版本来自镜像内置二进制的 /version 探测。",
        }
    )
    return info


async def collect_database_info(db: Any) -> dict[str, Any]:
    info: dict[str, Any] = {"dialect": None, "server_version": None, "migration_revision": None}
    try:
        bind = db.get_bind()
        info["dialect"] = str(bind.dialect.name)
    except Exception:  # noqa: BLE001
        pass
    from sqlalchemy import text

    dialect = info["dialect"]
    query = "SELECT sqlite_version()" if dialect == "sqlite" else "SHOW server_version"
    try:
        # SAVEPOINT 隔离错误，避免诊断查询把当前只读事务置为 failed。
        async with db.begin_nested():
            info["server_version"] = str(
                (await db.execute(text(query))).scalar_one_or_none() or ""
            )[:80] or None
    except Exception as exc:  # noqa: BLE001
        info["error"] = _short_error(exc)
    try:
        async with db.begin_nested():
            info["migration_revision"] = (
                await db.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            ).scalar_one_or_none()
    except Exception:  # noqa: BLE001
        pass
    return info


async def collect_redis_info() -> dict[str, Any]:
    try:
        from ..redis_client import get_redis

        server = await asyncio.wait_for(get_redis().info("server"), timeout=2.0)
    except Exception as exc:  # noqa: BLE001
        return {"server_version": None, "error": _short_error(exc)}
    if not isinstance(server, dict):
        return {"server_version": None}
    # 只挑版本字段；INFO 里的可执行路径、配置文件路径、run_id 等不返回。
    return {
        "server_version": str(server.get("redis_version") or "") or None,
        "mode": str(server.get("redis_mode") or "") or None,
    }


async def collect_runtime_versions(
    db: Any,
    *,
    package: str | None = None,
    probe_gateway: bool = True,
) -> dict[str, Any]:
    gateway, database, redis_info = await asyncio.gather(
        collect_gateway_info(probe_binary=probe_gateway),
        collect_database_info(db),
        collect_redis_info(),
    )
    result: dict[str, Any] = {
        "app": collect_app_info(),
        "python": collect_python_info(),
        "telethon": collect_telethon_details(),
        "gateway": gateway,
        "packages": collect_key_packages(),
        "workers": collect_worker_runtime(),
        "database": database,
        "redis": redis_info,
        "platform": collect_platform_info(),
    }
    if package:
        result["package_lookup"] = lookup_package(package)
    return result


__all__ = [
    "KEY_PACKAGES",
    "collect_app_info",
    "collect_database_info",
    "collect_gateway_info",
    "collect_key_packages",
    "collect_platform_info",
    "collect_python_info",
    "collect_redis_info",
    "collect_runtime_versions",
    "collect_telethon_details",
    "collect_worker_runtime",
    "lookup_package",
    "normalize_package_name",
    "package_version",
    "probe_gateway_binary_version",
]
