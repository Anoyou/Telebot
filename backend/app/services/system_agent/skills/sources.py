"""Agent Skill 安装来源：GitHub 仓库/子目录、zip（上传或链接）、粘贴 SKILL.md。

- GitHub：复用插件安装同一套 ``git``（浅克隆、超时、私有仓库凭据沿用已保存的插件仓库），
  支持 ``https://github.com/o/r``、``/tree/<分支>/<子目录>``、``/blob/<分支>/<子目录>/SKILL.md``；
  记录实际提交 SHA，更新时按原分支重新拉取。
- zip 链接：只允许公网 HTTPS，逐跳校验重定向目标并固定解析结果（与 System Agent 读网页同一套防护），
  响应超过上限立即中止。
- 所有来源最终都走 ``package`` 模块的同一套校验。网络失败直接报错，不自动重试。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .package import (
    MAX_ARCHIVE_BYTES,
    SKILL_ENTRY,
    SkillPackage,
    SkillValidationError,
    normalize_skill_path,
    package_from_directory,
    package_from_skill_md,
    package_from_zip,
)

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_GIT_TIMEOUT_SECONDS = 120.0
_ZIP_TIMEOUT = httpx.Timeout(30.0, connect=8.0)
_USER_AGENT = "Mozilla/5.0 (compatible; TelePilot-Skill-Installer/1.0)"


class SkillSourceError(Exception):
    """来源不可用（网络、git、URL 不合法等）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FetchedSkill:
    package: SkillPackage
    source_type: str
    source_url: str | None = None
    source_ref: str | None = None
    source_path: str | None = None
    source_commit: str | None = None


@dataclass(frozen=True)
class GithubLocation:
    owner: str
    repo: str
    # tree/blob 之后的全部路径段（分支名可能含 /，需结合远端 refs 拆分）
    rest: tuple[str, ...] = ()

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"

    @property
    def page_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


def parse_github_url(url: str) -> GithubLocation:
    raw = str(url or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() not in _GITHUB_HOSTS:
        raise SkillSourceError(
            "BAD_SOURCE_URL",
            "只支持 https://github.com/<用户>/<仓库> 形式的 GitHub 链接（可带 /tree/<分支>/<子目录>）。",
        )
    if parsed.username or parsed.password:
        raise SkillSourceError("BAD_SOURCE_URL", "链接不能包含用户名或密码。")
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise SkillSourceError("BAD_SOURCE_URL", "GitHub 链接缺少用户或仓库名。")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    safe = re.compile(r"^[A-Za-z0-9_.-]+$")
    if not safe.match(owner) or not safe.match(repo):
        raise SkillSourceError("BAD_SOURCE_URL", "GitHub 用户或仓库名包含不支持的字符。")
    rest: tuple[str, ...] = ()
    if len(parts) > 2:
        if parts[2] not in {"tree", "blob"} or len(parts) < 4:
            raise SkillSourceError(
                "BAD_SOURCE_URL",
                "无法识别的 GitHub 链接；请使用仓库首页或 /tree/<分支>/<子目录> 链接。",
            )
        rest = tuple(parts[3:])
        if parts[2] == "blob" and rest and rest[-1] == SKILL_ENTRY:
            rest = rest[:-1]
    return GithubLocation(owner=owner, repo=repo, rest=rest)


def split_ref_and_path(rest: tuple[str, ...], refs: Iterable[str]) -> tuple[str | None, str | None]:
    """按远端 refs 把 tree/ 之后的路径段拆成 (分支/标签, 子目录)；取最长匹配。"""

    if not rest:
        return None, None
    known = set(refs)
    for size in range(len(rest), 0, -1):
        candidate = "/".join(rest[:size])
        if candidate in known:
            path = "/".join(rest[size:]) or None
            return candidate, path
    # 未匹配到已知 ref：按第一段为分支（或提交 SHA）处理。
    return rest[0], "/".join(rest[1:]) or None


async def _run_git(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    from ...remote_plugin_service import GitOperationFailed
    from ...remote_plugin_service import _run_git as run_git

    try:
        return await run_git(*args, cwd=cwd, timeout=_GIT_TIMEOUT_SECONDS, env=env)
    except GitOperationFailed as exc:
        raise SkillSourceError(exc.code, exc.message) from None


async def _remote_refs(clone_url: str, env: dict[str, str] | None) -> set[str]:
    output = await _run_git("ls-remote", "--heads", "--tags", clone_url, env=env)
    refs: set[str] = set()
    for line in output.splitlines():
        _sha, _, ref = line.partition("\t")
        ref = ref.strip()
        if ref.endswith("^{}"):
            ref = ref[:-3]
        for prefix in ("refs/heads/", "refs/tags/"):
            if ref.startswith(prefix):
                refs.add(ref[len(prefix):])
    return refs


async def fetch_github(
    url: str,
    *,
    ref: str | None = None,
    path: str | None = None,
    git_env: dict[str, str] | None = None,
    reserved_names: Iterable[str] = (),
) -> FetchedSkill:
    location = parse_github_url(url)
    explicit_ref = str(ref or "").strip() or None
    explicit_path = str(path or "").strip().strip("/") or None
    resolved_ref = explicit_ref
    resolved_path = explicit_path
    if location.rest:
        refs = await _remote_refs(location.clone_url, git_env) if not explicit_ref else set()
        url_ref, url_path = split_ref_and_path(location.rest, refs)
        resolved_ref = explicit_ref or url_ref
        resolved_path = explicit_path or url_path
    if resolved_path:
        resolved_path = normalize_skill_path(resolved_path)
    if resolved_ref and (resolved_ref.startswith("-") or ".." in resolved_ref or len(resolved_ref) > 255):
        raise SkillSourceError("BAD_SOURCE_URL", "分支或标签名不合法。")
    workdir = Path(tempfile.mkdtemp(prefix="tp-skill-"))
    target = workdir / "repo"
    try:
        if resolved_ref and _SHA_RE.match(resolved_ref):
            target.mkdir()
            await _run_git("init", "-q", cwd=target, env=git_env)
            await _run_git("remote", "add", "origin", location.clone_url, cwd=target, env=git_env)
            await _run_git("fetch", "--depth", "1", "origin", resolved_ref, cwd=target, env=git_env)
            await _run_git("checkout", "-q", "FETCH_HEAD", cwd=target, env=git_env)
        else:
            args = ["clone", "--depth", "1", "--no-tags"]
            if resolved_ref:
                args.extend(["--branch", resolved_ref, "--single-branch"])
            args.extend([location.clone_url, str(target)])
            await _run_git(*args, env=git_env)
        commit = (await _run_git("rev-parse", "HEAD", cwd=target, env=git_env)).strip()[:64] or None
        package = await asyncio.to_thread(
            package_from_directory, target, subpath=resolved_path, reserved_names=tuple(reserved_names)
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return FetchedSkill(
        package=package,
        source_type="github",
        source_url=location.page_url,
        source_ref=resolved_ref,
        source_path=resolved_path,
        source_commit=commit,
    )


async def _download_public_bytes(raw_url: str, *, max_bytes: int) -> tuple[str, bytes]:
    """下载公网 HTTPS 文件：逐跳校验、固定解析地址、超限即停。"""

    from ..tools.web import _pinned_request_target, _URLReadError, _validate_public_url

    if urlsplit(str(raw_url or "").strip()).scheme.lower() != "https":
        raise SkillSourceError("BAD_SOURCE_URL", "zip 链接只支持 https://。")
    current = str(raw_url).strip()
    try:
        async with httpx.AsyncClient(timeout=_ZIP_TIMEOUT, follow_redirects=False, trust_env=False) as client:
            for hop in range(5):
                current, addresses = await _validate_public_url(current)
                if urlsplit(current).scheme != "https":
                    raise SkillSourceError("BAD_SOURCE_URL", "zip 链接重定向到了非 HTTPS 地址。")
                connect_url, host_header, sni = _pinned_request_target(current, addresses[0])
                async with client.stream(
                    "GET",
                    connect_url,
                    headers={"Host": host_header, "User-Agent": _USER_AGENT, "Accept": "*/*"},
                    extensions={"sni_hostname": sni},
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "").strip()
                        if not location or hop >= 4:
                            raise SkillSourceError("DOWNLOAD_FAILED", "zip 链接重定向异常。")
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        raise SkillSourceError(
                            "DOWNLOAD_FAILED", f"下载 zip 失败（HTTP {response.status_code}）。"
                        )
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > max_bytes:
                        raise SkillValidationError(
                            "ARCHIVE_TOO_LARGE", f"zip 超过 {max_bytes // (1024 * 1024)} MB 上限。"
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise SkillValidationError(
                                "ARCHIVE_TOO_LARGE", f"zip 超过 {max_bytes // (1024 * 1024)} MB 上限。"
                            )
                    return current, bytes(body)
    except _URLReadError as exc:
        raise SkillSourceError("BAD_SOURCE_URL", exc.message) from None
    except httpx.TimeoutException:
        raise SkillSourceError("DOWNLOAD_TIMEOUT", "下载 zip 超时。") from None
    except httpx.HTTPError:
        raise SkillSourceError("DOWNLOAD_FAILED", "无法连接 zip 链接。") from None
    raise SkillSourceError("DOWNLOAD_FAILED", "zip 链接没有返回内容。")


async def fetch_zip_url(
    url: str,
    *,
    path: str | None = None,
    reserved_names: Iterable[str] = (),
) -> FetchedSkill:
    _final_url, data = await _download_public_bytes(url, max_bytes=MAX_ARCHIVE_BYTES)
    package = package_from_zip(data, subpath=path, reserved_names=reserved_names)
    return FetchedSkill(
        package=package,
        source_type="zip_url",
        source_url=str(url).strip(),
        source_path=str(path or "").strip().strip("/") or None,
    )


def decode_zip_base64(value: str) -> bytes:
    raw = str(value or "").strip()
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    # base64 膨胀约 4/3；先按长度粗判，避免为超大输入分配内存。
    if len(raw) > MAX_ARCHIVE_BYTES * 4 // 3 + 16:
        raise SkillValidationError(
            "ARCHIVE_TOO_LARGE", f"zip 超过 {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB 上限。"
        )
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise SkillValidationError("BAD_ARCHIVE", "zip 内容不是合法的 base64。") from None


def fetch_zip_upload(
    zip_base64: str,
    *,
    path: str | None = None,
    reserved_names: Iterable[str] = (),
) -> FetchedSkill:
    package = package_from_zip(decode_zip_base64(zip_base64), subpath=path, reserved_names=reserved_names)
    return FetchedSkill(
        package=package,
        source_type="zip_upload",
        source_path=str(path or "").strip().strip("/") or None,
    )


def fetch_paste(skill_md: str, *, reserved_names: Iterable[str] = ()) -> FetchedSkill:
    return FetchedSkill(
        package=package_from_skill_md(skill_md, reserved_names=reserved_names),
        source_type="paste",
    )


__all__ = [
    "FetchedSkill",
    "GithubLocation",
    "SkillSourceError",
    "decode_zip_base64",
    "fetch_github",
    "fetch_paste",
    "fetch_zip_upload",
    "fetch_zip_url",
    "parse_github_url",
    "split_ref_and_path",
]
