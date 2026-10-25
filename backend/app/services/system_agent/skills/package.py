"""Agent Skill 包解析与校验（纯函数，不访问网络与数据库）。

格式兼容 Anthropic Agent Skills：一个目录，根部必须有 ``SKILL.md``，以 YAML front matter
开头并至少声明 ``name`` 与 ``description``；目录里可以有附属 Markdown、参考资料和脚本。

安全约束（所有安装来源共用）：
- 只保存 UTF-8 文本文件；二进制、符号链接、隐藏目录、超大文件跳过并如实记录原因。
- 拒绝绝对路径、``..``、反斜杠、空字节等路径穿越写法。
- 单文件、单 skill 总量、文件数都有上限；zip 解压按声明大小预检并按上限读取（防 zip 炸弹）。
- 脚本只作为说明文本保存，平台从不执行。
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SKILL_ENTRY = "SKILL.md"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 1024
MAX_SKILL_FILES = 64
MAX_FILE_BYTES = 256 * 1024
MAX_SKILL_TOTAL_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5000
MAX_PATH_CHARS = 512
MAX_CANDIDATES_SHOWN = 20

_BINARY_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff",
        ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".tar",
        ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".jar", ".pyc", ".o", ".a",
        ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".wav",
        ".ogg", ".webm", ".avi", ".psd", ".sqlite", ".db", ".docx", ".xlsx", ".pptx",
    }
)
_IGNORED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
_IGNORED_DIRS = frozenset({"__MACOSX", ".git", "__pycache__", "node_modules", ".github"})


class SkillValidationError(ValueError):
    """skill 包不符合格式或安全约束；``code`` 供 API 返回。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SkillPackage:
    """校验通过的 skill 包。``files`` 含 ``SKILL.md``，路径为 POSIX 相对路径。"""

    name: str
    description: str
    version: str | None
    license: str | None
    files: dict[str, str]
    skipped: list[dict[str, str]] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(len(content.encode("utf-8")) for content in self.files.values())

    @property
    def sha256(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.files):
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(self.files[path].encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    @property
    def body(self) -> str:
        return split_front_matter(self.files[SKILL_ENTRY])[1]


def normalize_skill_path(raw: str) -> str:
    """把包内路径规范化为安全的 POSIX 相对路径；不安全时抛 SkillValidationError。"""

    value = str(raw or "")
    if not value or "\x00" in value or "\\" in value:
        raise SkillValidationError("UNSAFE_PATH", f"文件路径不安全：{value[:80]!r}")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise SkillValidationError("UNSAFE_PATH", f"不允许绝对路径：{value[:80]!r}")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise SkillValidationError("UNSAFE_PATH", f"不允许 .. 等路径穿越：{value[:80]!r}")
    normalized = "/".join(parts)
    if len(normalized) > MAX_PATH_CHARS:
        raise SkillValidationError("UNSAFE_PATH", "文件路径过长。")
    return normalized


def split_front_matter(text: str) -> tuple[str, str]:
    """返回 (front matter 原文, 正文)。没有 front matter 时前者为空串。"""

    value = str(text or "").lstrip("\ufeff")
    if not value.startswith("---"):
        return "", value
    lines = value.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", value
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            return "".join(lines[1:index]), "".join(lines[index + 1 :]).lstrip("\r\n")
    return "", value


def parse_skill_md(text: str) -> dict[str, Any]:
    """解析 SKILL.md front matter 并校验 name/description。"""

    raw, _body = split_front_matter(text)
    if not raw.strip():
        raise SkillValidationError(
            "MISSING_FRONT_MATTER",
            "SKILL.md 必须以 YAML front matter 开头（--- 包裹），并声明 name 与 description。",
        )
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SkillValidationError("BAD_FRONT_MATTER", f"SKILL.md front matter 不是合法 YAML：{exc}") from None
    if not isinstance(meta, dict):
        raise SkillValidationError("BAD_FRONT_MATTER", "SKILL.md front matter 必须是键值对。")
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not name.strip():
        raise SkillValidationError("BAD_NAME", "front matter 缺少 name。")
    name = name.strip()
    if len(name) > MAX_NAME_CHARS or not NAME_RE.match(name):
        raise SkillValidationError(
            "BAD_NAME",
            f"name「{name[:80]}」不合法：只能用小写字母、数字和连字符，最长 {MAX_NAME_CHARS} 个字符。",
        )
    if not isinstance(description, str) or not description.strip():
        raise SkillValidationError("BAD_DESCRIPTION", "front matter 缺少 description。")
    description = " ".join(description.split())
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillValidationError(
            "BAD_DESCRIPTION", f"description 不能超过 {MAX_DESCRIPTION_CHARS} 个字符。"
        )
    version = meta.get("version")
    metadata = meta.get("metadata")
    if version is None and isinstance(metadata, dict):
        version = metadata.get("version")
    license_value = meta.get("license")
    return {
        "name": name,
        "description": description,
        "version": str(version).strip()[:64] if version not in (None, "") else None,
        "license": str(license_value).strip()[:200] if license_value not in (None, "") else None,
    }


def _decode_text(data: bytes) -> str | None:
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return None


def _skip_reason_for_name(path: str) -> str | None:
    parts = path.split("/")
    if any(part in _IGNORED_DIRS for part in parts[:-1]):
        return "ignored_dir"
    if parts[-1] in _IGNORED_NAMES:
        return "ignored_file"
    if any(part.startswith(".") for part in parts):
        return "hidden"
    if posixpath.splitext(parts[-1])[1].lower() in _BINARY_SUFFIXES:
        return "binary"
    return None


def build_package(
    raw_files: Mapping[str, bytes],
    *,
    skipped: Iterable[dict[str, str]] = (),
    reserved_names: Iterable[str] = (),
) -> SkillPackage:
    """从 {相对路径: 字节} 构造并校验 SkillPackage。"""

    files: dict[str, str] = {}
    skipped_list: list[dict[str, str]] = [dict(item) for item in skipped]
    total = 0
    for raw_path in sorted(raw_files):
        path = normalize_skill_path(raw_path)
        data = raw_files[raw_path]
        reason = _skip_reason_for_name(path)
        if reason:
            skipped_list.append({"path": path, "reason": reason})
            continue
        if len(data) > MAX_FILE_BYTES:
            if path == SKILL_ENTRY:
                raise SkillValidationError(
                    "FILE_TOO_LARGE", f"SKILL.md 超过单文件上限 {MAX_FILE_BYTES // 1024} KB。"
                )
            skipped_list.append({"path": path, "reason": "too_large"})
            continue
        text = _decode_text(data)
        if text is None:
            if path == SKILL_ENTRY:
                raise SkillValidationError("BAD_ENCODING", "SKILL.md 必须是 UTF-8 文本。")
            skipped_list.append({"path": path, "reason": "binary"})
            continue
        if path in files:
            raise SkillValidationError("DUPLICATE_PATH", f"重复的文件路径：{path}")
        if len(files) >= MAX_SKILL_FILES:
            raise SkillValidationError(
                "TOO_MANY_FILES", f"skill 文本文件超过 {MAX_SKILL_FILES} 个上限。"
            )
        total += len(text.encode("utf-8"))
        if total > MAX_SKILL_TOTAL_BYTES:
            raise SkillValidationError(
                "SKILL_TOO_LARGE",
                f"skill 文本总量超过 {MAX_SKILL_TOTAL_BYTES // 1024} KB 上限。",
            )
        files[path] = text
    if SKILL_ENTRY not in files:
        raise SkillValidationError("MISSING_SKILL_MD", "skill 目录根部缺少 SKILL.md。")
    meta = parse_skill_md(files[SKILL_ENTRY])
    reserved = {str(item) for item in reserved_names}
    if meta["name"] in reserved:
        raise SkillValidationError(
            "RESERVED_NAME", f"name「{meta['name']}」与内置 skill 重名，请改名后再安装。"
        )
    return SkillPackage(
        name=meta["name"],
        description=meta["description"],
        version=meta["version"],
        license=meta["license"],
        files=files,
        skipped=skipped_list[:200],
    )


def package_from_skill_md(text: str, *, reserved_names: Iterable[str] = ()) -> SkillPackage:
    """直接粘贴 SKILL.md 内容安装。"""

    value = str(text or "")
    if not value.strip():
        raise SkillValidationError("EMPTY_SKILL_MD", "SKILL.md 内容不能为空。")
    return build_package({SKILL_ENTRY: value.encode("utf-8")}, reserved_names=reserved_names)


def _candidate_roots(paths: Iterable[str]) -> list[str]:
    roots = []
    for path in paths:
        parts = path.split("/")
        if parts[-1] == SKILL_ENTRY and _skip_reason_for_name(path) is None:
            roots.append("/".join(parts[:-1]))
    return sorted(set(roots), key=lambda item: (item.count("/") if item else -1, item))


def select_skill_root(paths: Iterable[str], subpath: str | None) -> str:
    """在包内路径中定位 skill 根目录（含 SKILL.md 的目录），返回 "" 表示包根。"""

    all_paths = list(paths)
    if subpath:
        root = normalize_skill_path(subpath)
        if root.endswith("/" + SKILL_ENTRY) or root == SKILL_ENTRY:
            root = root[: -len(SKILL_ENTRY)].rstrip("/")
        entry = f"{root}/{SKILL_ENTRY}" if root else SKILL_ENTRY
        if entry not in all_paths:
            candidates = _candidate_roots(all_paths)
            raise SkillValidationError(
                "MISSING_SKILL_MD",
                f"路径「{root or '/'}」下没有 SKILL.md。"
                + (f"找到这些 skill 目录：{'、'.join(c or '/' for c in candidates[:MAX_CANDIDATES_SHOWN])}" if candidates else ""),
            )
        return root
    candidates = _candidate_roots(all_paths)
    if not candidates:
        raise SkillValidationError("MISSING_SKILL_MD", "没有找到 SKILL.md。")
    if "" in candidates:
        return ""
    shallowest = candidates[0].count("/")
    top = [item for item in candidates if item.count("/") == shallowest]
    if len(top) > 1:
        raise SkillValidationError(
            "MULTIPLE_SKILLS",
            "包含多个 skill，请指定要安装的子目录："
            + "、".join(top[:MAX_CANDIDATES_SHOWN]),
        )
    return top[0]


def _strip_common_prefix(names: list[str]) -> str:
    """GitHub/打包工具常见的单一顶层目录（如 repo-main/），返回需要去掉的前缀。"""

    tops = {name.split("/", 1)[0] for name in names if name}
    if len(tops) == 1 and all("/" in name for name in names):
        return next(iter(tops)) + "/"
    return ""


def package_from_zip(
    data: bytes,
    *,
    subpath: str | None = None,
    reserved_names: Iterable[str] = (),
) -> SkillPackage:
    """从 zip 字节解析 skill；只读取 skill 根目录内的条目。"""

    if len(data) > MAX_ARCHIVE_BYTES:
        raise SkillValidationError(
            "ARCHIVE_TOO_LARGE", f"zip 超过 {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB 上限。"
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SkillValidationError("BAD_ARCHIVE", "不是有效的 zip 文件。") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise SkillValidationError("ARCHIVE_TOO_LARGE", "zip 内条目过多。")
        entries: dict[str, zipfile.ZipInfo] = {}
        skipped: list[dict[str, str]] = []
        for info in infos:
            if info.is_dir():
                continue
            # 先做穿越校验：任何不安全条目都直接拒绝整个包。
            normalized = normalize_skill_path(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                skipped.append({"path": normalized, "reason": "symlink"})
                continue
            entries[normalized] = info
        prefix = _strip_common_prefix(list(entries))
        if prefix and subpath:
            # 用户可能按含顶层目录的完整路径指定子目录：两种写法都接受。
            try:
                select_skill_root(entries.keys(), subpath)
                prefix = ""
            except SkillValidationError:
                pass
        if prefix:
            entries = {name[len(prefix):]: info for name, info in entries.items()}
            skipped = [
                {**item, "path": item["path"][len(prefix):]} if item["path"].startswith(prefix) else item
                for item in skipped
            ]
        root = select_skill_root(entries.keys(), subpath)
        root_prefix = f"{root}/" if root else ""
        raw_files: dict[str, bytes] = {}
        declared_total = 0
        selected_skips = [
            {**item, "path": item["path"][len(root_prefix):]}
            for item in skipped
            if item["path"].startswith(root_prefix)
        ]
        for name, info in entries.items():
            if not name.startswith(root_prefix):
                continue
            relative = name[len(root_prefix):]
            if _skip_reason_for_name(relative):
                selected_skips.append({"path": relative, "reason": _skip_reason_for_name(relative) or ""})
                continue
            if info.file_size > MAX_FILE_BYTES:
                if relative == SKILL_ENTRY:
                    raise SkillValidationError(
                        "FILE_TOO_LARGE", f"SKILL.md 超过单文件上限 {MAX_FILE_BYTES // 1024} KB。"
                    )
                selected_skips.append({"path": relative, "reason": "too_large"})
                continue
            declared_total += info.file_size
            if declared_total > MAX_SKILL_TOTAL_BYTES * 4:
                raise SkillValidationError("SKILL_TOO_LARGE", "skill 目录内容过大。")
            with archive.open(info) as handle:
                content = handle.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                selected_skips.append({"path": relative, "reason": "too_large"})
                continue
            raw_files[relative] = content
    return build_package(raw_files, skipped=selected_skips, reserved_names=reserved_names)


def package_from_directory(
    directory: Path,
    *,
    subpath: str | None = None,
    reserved_names: Iterable[str] = (),
) -> SkillPackage:
    """从本地目录（git clone 结果）读取 skill；不跟随符号链接，不读目录外文件。"""

    base = directory.resolve()
    listing: list[str] = []
    symlinks: list[str] = []
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base).as_posix()
        if relative.split("/", 1)[0] == ".git":
            continue
        if path.is_symlink():
            symlinks.append(relative)
            continue
        if path.is_file():
            listing.append(relative)
        if len(listing) > MAX_ARCHIVE_ENTRIES * 4:
            raise SkillValidationError("ARCHIVE_TOO_LARGE", "仓库文件过多，请指定 skill 子目录。")
    root = select_skill_root(listing, subpath)
    root_prefix = f"{root}/" if root else ""
    raw_files: dict[str, bytes] = {}
    skipped: list[dict[str, str]] = [
        {"path": item[len(root_prefix):], "reason": "symlink"}
        for item in symlinks
        if item.startswith(root_prefix)
    ]
    for relative in listing:
        if not relative.startswith(root_prefix):
            continue
        inner = relative[len(root_prefix):]
        reason = _skip_reason_for_name(inner)
        if reason:
            skipped.append({"path": inner, "reason": reason})
            continue
        target = (base / relative).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            skipped.append({"path": inner, "reason": "outside"})
            continue
        size = target.stat().st_size
        if size > MAX_FILE_BYTES:
            if inner == SKILL_ENTRY:
                raise SkillValidationError(
                    "FILE_TOO_LARGE", f"SKILL.md 超过单文件上限 {MAX_FILE_BYTES // 1024} KB。"
                )
            skipped.append({"path": inner, "reason": "too_large"})
            continue
        raw_files[inner] = target.read_bytes()
    return build_package(raw_files, skipped=skipped, reserved_names=reserved_names)


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    value = str(text or "")
    if max_chars <= 0 or len(value) <= max_chars:
        return value, False
    return value[:max_chars].rstrip() + "\n…（内容过长已截断）", True


__all__ = [
    "MAX_ARCHIVE_BYTES",
    "MAX_DESCRIPTION_CHARS",
    "MAX_FILE_BYTES",
    "MAX_NAME_CHARS",
    "MAX_SKILL_FILES",
    "MAX_SKILL_TOTAL_BYTES",
    "NAME_RE",
    "SKILL_ENTRY",
    "SkillPackage",
    "SkillValidationError",
    "build_package",
    "normalize_skill_path",
    "package_from_directory",
    "package_from_skill_md",
    "package_from_zip",
    "parse_skill_md",
    "select_skill_root",
    "split_front_matter",
    "truncate_text",
]
