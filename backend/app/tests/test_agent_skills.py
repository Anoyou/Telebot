"""Agent Skills：包校验、安装来源解析、安装/卸载解绑、启用范围与渐进加载。"""

from __future__ import annotations

import base64
import io
import stat
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import agent_skills as api
from app.db.models.agent_skill import AgentSkill, AgentSkillFile
from app.db.models.feature import AccountFeature, Feature
from app.db.models.plugin_global_config import PluginGlobalConfig
from app.db.models.system import SystemSetting
from app.schemas.agent_skill import AgentSkillEnablePatch, AgentSkillInstallIn, AgentSkillUpdateIn
from app.services.system_agent.skills import BUILTIN_SKILLS, SkillRegistry, store
from app.services.system_agent.skills import package as pkg
from app.services.system_agent.skills.sources import (
    SkillSourceError,
    fetch_paste,
    fetch_zip_upload,
    parse_github_url,
    split_ref_and_path,
)
from app.services.system_agent.tool_routing import ToolRoute
from app.worker.plugins.skills_facade import PluginSkills, PluginSkillUnavailable

SKILL_MD = """---
name: pdf-helper
description: 处理 PDF 表单与文本提取的步骤说明。
license: MIT
metadata:
  version: "1.2"
---
# PDF Helper

需要表单时读取 reference/forms.md。
"""


def _zip(files: dict[str, bytes | str], *, symlinks: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, target)
    return buffer.getvalue()


# ─────────────────────────── 包校验 ───────────────────────────
def test_parse_skill_md_front_matter_and_version() -> None:
    meta = pkg.parse_skill_md(SKILL_MD)
    assert meta["name"] == "pdf-helper"
    assert meta["version"] == "1.2"
    assert meta["license"] == "MIT"
    assert "PDF Helper" in pkg.split_front_matter(SKILL_MD)[1]


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("# 没有 front matter", "MISSING_FRONT_MATTER"),
        ("---\nname: Bad_Name\ndescription: x\n---\n", "BAD_NAME"),
        ("---\nname: ok\n---\n", "BAD_DESCRIPTION"),
        ("---\nname: [unclosed\n---\n", "BAD_FRONT_MATTER"),
        ("---\n- a\n- b\n---\n", "BAD_FRONT_MATTER"),
    ],
)
def test_parse_skill_md_rejects_invalid(content: str, code: str) -> None:
    with pytest.raises(pkg.SkillValidationError) as info:
        pkg.parse_skill_md(content)
    assert info.value.code == code


@pytest.mark.parametrize("path", ["../evil.md", "/etc/passwd", "a/../../b", "C:/x", "a\\b", "a\x00b"])
def test_normalize_path_rejects_traversal(path: str) -> None:
    with pytest.raises(pkg.SkillValidationError) as info:
        pkg.normalize_skill_path(path)
    assert info.value.code == "UNSAFE_PATH"


def test_zip_with_traversal_entry_is_rejected() -> None:
    data = _zip({"SKILL.md": SKILL_MD, "../../outside.md": "x"})
    with pytest.raises(pkg.SkillValidationError) as info:
        pkg.package_from_zip(data)
    assert info.value.code == "UNSAFE_PATH"


def test_zip_strips_top_dir_skips_binary_symlink_and_hidden() -> None:
    data = _zip(
        {
            "repo-main/SKILL.md": SKILL_MD,
            "repo-main/reference/forms.md": "表单说明",
            "repo-main/scripts/fill.py": "print('never executed')",
            "repo-main/logo.png": b"\x89PNG\r\n",
            "repo-main/.env": "SECRET=1",
            "repo-main/blob.dat": b"\x00\x01\x02",
        },
        symlinks={"repo-main/link.md": "/etc/passwd"},
    )
    package = pkg.package_from_zip(data)
    assert package.name == "pdf-helper"
    assert set(package.files) == {"SKILL.md", "reference/forms.md", "scripts/fill.py"}
    reasons = {item["path"]: item["reason"] for item in package.skipped}
    assert reasons["logo.png"] == "binary"
    assert reasons["link.md"] == "symlink"
    assert reasons[".env"] == "hidden"
    assert reasons["blob.dat"] == "binary"


def test_zip_multiple_skills_requires_subpath() -> None:
    other = SKILL_MD.replace("pdf-helper", "docx-helper")
    data = _zip({"r/skills/pdf/SKILL.md": SKILL_MD, "r/skills/docx/SKILL.md": other})
    with pytest.raises(pkg.SkillValidationError) as info:
        pkg.package_from_zip(data)
    assert info.value.code == "MULTIPLE_SKILLS"
    assert pkg.package_from_zip(data, subpath="skills/docx").name == "docx-helper"
    # 带顶层目录的完整路径同样可用
    assert pkg.package_from_zip(data, subpath="r/skills/pdf/SKILL.md").name == "pdf-helper"
    with pytest.raises(pkg.SkillValidationError) as missing:
        pkg.package_from_zip(data, subpath="skills/xlsx")
    assert "skills/pdf" in missing.value.message


def test_limits_file_count_and_size() -> None:
    files: dict[str, bytes] = {"SKILL.md": SKILL_MD.encode()}
    files.update({f"ref/{i}.md": b"x" for i in range(pkg.MAX_SKILL_FILES)})
    with pytest.raises(pkg.SkillValidationError) as info:
        pkg.build_package(files)
    assert info.value.code == "TOO_MANY_FILES"
    big = {"SKILL.md": SKILL_MD.encode(), "huge.md": b"a" * (pkg.MAX_FILE_BYTES + 1)}
    package = pkg.build_package(big)
    assert {"path": "huge.md", "reason": "too_large"} in package.skipped
    with pytest.raises(pkg.SkillValidationError) as entry:
        pkg.build_package({"SKILL.md": b"---\n" + b"a" * (pkg.MAX_FILE_BYTES + 1)})
    assert entry.value.code == "FILE_TOO_LARGE"


def test_zip_bomb_declared_size_and_archive_size() -> None:
    with pytest.raises(pkg.SkillValidationError) as info:
        pkg.package_from_zip(b"0" * (pkg.MAX_ARCHIVE_BYTES + 1))
    assert info.value.code == "ARCHIVE_TOO_LARGE"
    with pytest.raises(pkg.SkillValidationError):
        pkg.package_from_zip(b"not a zip")


def test_reserved_builtin_name_rejected() -> None:
    builtin = BUILTIN_SKILLS[0].name
    with pytest.raises(pkg.SkillValidationError) as info:
        pkg.package_from_skill_md(SKILL_MD.replace("pdf-helper", builtin), reserved_names=[builtin])
    assert info.value.code == "RESERVED_NAME"


def test_package_from_directory_ignores_symlink_outside(tmp_path) -> None:
    root = tmp_path / "repo"
    (root / "skills" / "pdf").mkdir(parents=True)
    (root / "skills" / "pdf" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    (root / "skills" / "pdf" / "leak.md").symlink_to(secret)
    package = pkg.package_from_directory(root, subpath="skills/pdf")
    assert set(package.files) == {"SKILL.md"}
    assert {"path": "leak.md", "reason": "symlink"} in package.skipped


# ─────────────────────────── 来源解析 ───────────────────────────
def test_parse_github_urls() -> None:
    plain = parse_github_url("https://github.com/anthropics/skills")
    assert plain.clone_url == "https://github.com/anthropics/skills.git"
    assert plain.rest == ()
    tree = parse_github_url("https://github.com/anthropics/skills/tree/main/skills/pdf")
    assert tree.rest == ("main", "skills", "pdf")
    blob = parse_github_url("https://github.com/o/r/blob/feature/x/skills/pdf/SKILL.md")
    assert blob.rest == ("feature", "x", "skills", "pdf")
    for bad in (
        "http://github.com/o/r",
        "https://gitlab.com/o/r",
        "https://github.com/o",
        "https://user:pw@github.com/o/r",
        "https://github.com/o/r/issues/1",
    ):
        with pytest.raises(SkillSourceError):
            parse_github_url(bad)


def test_split_ref_prefers_longest_known_ref() -> None:
    rest = ("feature", "x", "skills", "pdf")
    assert split_ref_and_path(rest, {"feature/x", "main"}) == ("feature/x", "skills/pdf")
    assert split_ref_and_path(("main", "skills", "pdf"), set()) == ("main", "skills/pdf")
    assert split_ref_and_path((), {"main"}) == (None, None)


# ─────────────────────────── 数据库 ───────────────────────────
@pytest.fixture
async def db() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in (
            AgentSkill.__table__,
            AgentSkillFile.__table__,
            SystemSetting.__table__,
            Feature.__table__,
            AccountFeature.__table__,
        ):
            await conn.run_sync(lambda sync, t=table: t.create(sync, checkfirst=True))
        await conn.execute(
            text(
                "CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
                "action TEXT NOT NULL, target TEXT, detail JSON, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE plugin_global_config (plugin_key TEXT PRIMARY KEY, "
                "config JSON NOT NULL DEFAULT '{}', updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_install_scope_and_progressive_load(db: AsyncSession) -> None:
    fetched = fetch_zip_upload(
        base64.b64encode(_zip({"SKILL.md": SKILL_MD, "reference/forms.md": "表单步骤"})).decode()
    )
    row = await store.install(db, fetched, user_id=1)
    assert row.enabled_agent is True and row.enabled_plugins is False
    with pytest.raises(store.SkillConflict) as dup:
        await store.install(db, fetched, user_id=1)
    assert dup.value.code == "SKILL_EXISTS"

    agent_catalog = await store.catalog(db, store.SCOPE_AGENT)
    assert [entry.name for entry in agent_catalog] == ["pdf-helper"]
    assert await store.catalog(db, store.SCOPE_PLUGIN) == []
    prompt = store.render_catalog_prompt(agent_catalog)
    assert "pdf-helper：处理 PDF" in prompt and "skills.load" in prompt
    assert "PDF Helper" not in prompt  # 第一层只放 name + description

    loaded = await store.load_skill(db, "pdf-helper", scope=store.SCOPE_AGENT)
    assert loaded["content"].startswith("# PDF Helper")
    assert "name: pdf-helper" not in loaded["content"]
    assert loaded["files"] == [{"path": "reference/forms.md", "size": len("表单步骤".encode())}]
    file_ = await store.read_skill_file(db, "pdf-helper", "reference/forms.md", scope=store.SCOPE_AGENT)
    assert file_["content"] == "表单步骤"
    with pytest.raises(store.SkillNotFound):
        await store.read_skill_file(db, "pdf-helper", "../SKILL.md", scope=store.SCOPE_AGENT)
    # 插件未授权时读不到
    with pytest.raises(store.SkillNotFound):
        await store.load_skill(db, "pdf-helper", scope=store.SCOPE_PLUGIN)

    await store.set_enabled(db, "pdf-helper", enabled_agent=False, enabled_plugins=True, user_id=1)
    assert await store.catalog(db, store.SCOPE_AGENT) == []
    with pytest.raises(store.SkillNotFound):
        await store.load_skill(db, "pdf-helper", scope=store.SCOPE_AGENT)
    truncated = await store.load_skill(db, "pdf-helper", scope=store.SCOPE_PLUGIN, max_chars=5)
    assert truncated["truncated"] is True


@pytest.mark.asyncio
async def test_builtin_cannot_uninstall_but_can_disable(db: AsyncSession) -> None:
    builtin = BUILTIN_SKILLS[0].name
    with pytest.raises(store.SkillConflict):
        await store.uninstall(db, builtin, user_id=1)
    with pytest.raises(store.SkillConflict):
        await store.set_enabled(db, builtin, enabled_plugins=True, user_id=1)
    view = await store.set_enabled(db, builtin, enabled_agent=False, user_id=1)
    assert view["enabled_agent"] is False and view["builtin"] is True
    assert builtin in await store.disabled_builtin_names(db)
    registry = SkillRegistry(BUILTIN_SKILLS).without(await store.disabled_builtin_names(db))
    assert builtin not in {skill.name for skill in registry.list_all()}
    domain = BUILTIN_SKILLS[0].domains[0]
    assert all(skill.name != builtin for skill in registry.select(ToolRoute((domain,), "local", "t")))
    await store.set_enabled(db, builtin, enabled_agent=True, user_id=1)
    assert builtin not in await store.disabled_builtin_names(db)


@pytest.mark.asyncio
async def test_uninstall_unbinds_from_plugin_configs(db: AsyncSession) -> None:
    await store.install(db, fetch_paste(SKILL_MD), user_id=1, enabled_plugins=True)
    db.add(
        Feature(
            key="interaction-ai-chat",
            display_name="AI",
            manifest={
                "config_schema": {
                    "properties": {
                        "skills": {"type": "array", "x-ui-widget": store.SKILL_SELECT_WIDGET},
                        "other": {"type": "array"},
                    }
                }
            },
        )
    )
    db.add(Feature(key="plain", display_name="P", manifest={"config_schema": {"properties": {}}}))
    db.add(AccountFeature(account_id=7, feature_key="interaction-ai-chat", config={"skills": ["pdf-helper", "x"], "other": ["pdf-helper"]}))
    db.add(PluginGlobalConfig(plugin_key="interaction-ai-chat", config={"skills": ["pdf-helper"]}))
    await db.flush()

    result = await store.uninstall(db, "pdf-helper", user_id=1)
    assert result["uninstalled"] is True
    scopes = {(item["scope"], item["account_id"]) for item in result["unbound"]}
    assert scopes == {("global", None), ("account", 7)}
    account = await db.get(AccountFeature, (7, "interaction-ai-chat"))
    assert account.config["skills"] == ["x"]
    assert account.config["other"] == ["pdf-helper"]  # 非 skill 字段不动
    glob = await db.get(PluginGlobalConfig, "interaction-ai-chat")
    assert glob.config["skills"] == []
    assert await store.list_installed(db) == []
    assert (await db.execute(text("SELECT COUNT(*) FROM agent_skill_file"))).scalar() == 0
    actions = (await db.execute(text("SELECT action FROM audit_log ORDER BY id"))).scalars().all()
    assert actions == ["agent_skill.install", "agent_skill.uninstall"]


@pytest.mark.asyncio
async def test_replace_requires_same_name_and_detects_change(db: AsyncSession) -> None:
    row = await store.install(db, fetch_paste(SKILL_MD), user_id=1)
    assert await store.replace(db, row, fetch_paste(SKILL_MD), user_id=1) is False
    changed = await store.replace(db, row, fetch_paste(SKILL_MD + "\n新增一行\n"), user_id=1)
    assert changed is True
    with pytest.raises(store.SkillConflict) as info:
        await store.replace(db, row, fetch_paste(SKILL_MD.replace("pdf-helper", "renamed")), user_id=1)
    assert info.value.code == "NAME_CHANGED"


@pytest.mark.asyncio
async def test_plugin_facade_only_exposes_plugin_enabled(db: AsyncSession) -> None:
    await store.install(db, fetch_paste(SKILL_MD), user_id=1, enabled_plugins=True)
    await store.install(
        db, fetch_paste(SKILL_MD.replace("pdf-helper", "agent-only")), user_id=1, enabled_plugins=False
    )
    await db.flush()

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_exc):
            return False

    facade = PluginSkills(plugin_key="interaction-ai-chat", session_factory=_Ctx)
    listed = await facade.list()
    assert [item["name"] for item in listed] == ["pdf-helper"]
    assert await facade.list(names=["agent-only"]) == []
    assert await facade.list(names=[]) == []
    loaded = await facade.load("pdf-helper", max_chars=10_000_000)
    assert loaded["content"].startswith("# PDF Helper")
    with pytest.raises(PluginSkillUnavailable):
        await facade.load("agent-only")
    with pytest.raises(PluginSkillUnavailable):
        await facade.read_file("pdf-helper", "../../etc/passwd")


# ─────────────────────────── API ───────────────────────────
@pytest.mark.asyncio
async def test_api_install_patch_update_uninstall(db: AsyncSession) -> None:
    user = SimpleNamespace(id=9)
    created = await api.install_agent_skill(AgentSkillInstallIn(source="paste", skill_md=SKILL_MD), db, user)
    assert created["name"] == "pdf-helper" and created["source_type"] == "paste"
    listing = await api.list_agent_skills(db, user)
    names = {item["name"]: item for item in listing["items"]}
    assert names["pdf-helper"]["builtin"] is False
    assert names[BUILTIN_SKILLS[0].name]["builtin"] is True
    assert listing["limits"]["max_files"] == pkg.MAX_SKILL_FILES

    detail = await api.get_agent_skill("pdf-helper", db, user)
    assert detail["skill_md"].startswith("---") and detail["files"][0]["path"] == "SKILL.md"
    with pytest.raises(HTTPException) as dup:
        await api.install_agent_skill(AgentSkillInstallIn(source="paste", skill_md=SKILL_MD), db, user)
    assert dup.value.status_code == 409
    with pytest.raises(HTTPException) as bad:
        await api.install_agent_skill(AgentSkillInstallIn(source="paste", skill_md="no front matter"), db, user)
    assert bad.value.status_code == 400 and bad.value.detail["code"] == "MISSING_FRONT_MATTER"

    patched = await api.patch_agent_skill("pdf-helper", AgentSkillEnablePatch(enabled_plugins=True), db, user)
    assert patched["enabled_plugins"] is True and patched["enabled_agent"] is True
    with pytest.raises(HTTPException) as needs:
        await api.update_agent_skill("pdf-helper", AgentSkillUpdateIn(), db, user)
    assert needs.value.detail["code"] == "UPDATE_NEEDS_CONTENT"
    updated = await api.update_agent_skill(
        "pdf-helper", AgentSkillUpdateIn(skill_md=SKILL_MD + "\n更新\n"), db, user
    )
    assert updated["changed"] is True

    with pytest.raises(HTTPException) as builtin:
        await api.uninstall_agent_skill(BUILTIN_SKILLS[0].name, db, user)
    assert builtin.value.status_code == 409
    removed = await api.uninstall_agent_skill("pdf-helper", db, user)
    assert removed["uninstalled"] is True
    with pytest.raises(HTTPException) as gone:
        await api.get_agent_skill("pdf-helper", db, user)
    assert gone.value.status_code == 404


# ─────────────────────────── 插件内置 skill（覆盖层） ───────────────────────────
BUNDLED_V1 = SKILL_MD.replace("pdf-helper", "group-helper")
BUNDLED_V2 = BUNDLED_V1 + "\n插件 0.5 新增的一行。\n"


def _bundle_dir(tmp_path, content: str):
    plugin_dir = tmp_path / "plugin"
    (plugin_dir / "skills" / "group-helper").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "skills" / "group-helper" / "SKILL.md").write_text(content, encoding="utf-8")
    return plugin_dir


@pytest.mark.asyncio
async def test_bundled_sync_overlay_upstream_and_restore(db: AsyncSession, tmp_path) -> None:
    from app.worker.plugins.skills_facade import read_bundled_packages

    packages = read_bundled_packages(_bundle_dir(tmp_path, BUNDLED_V1))
    assert [item.name for item in packages] == ["group-helper"]
    assert (await store.sync_bundled(db, "interaction-ai-chat", packages))[0]["status"] == "installed"
    row = await store.get_installed(db, "group-helper")
    assert row.enabled_plugins is True and row.enabled_agent is False  # 默认仅对插件启用
    view = store.skill_view(row)
    assert view["bundled"] and view["editable"] and not view["uninstallable"]
    with pytest.raises(store.SkillConflict) as info:
        await store.uninstall(db, "group-helper", user_id=1)
    assert info.value.code == "BUNDLED_SKILL"

    # 用户未编辑：插件升级直接更新
    v2 = read_bundled_packages(_bundle_dir(tmp_path, BUNDLED_V2))
    assert (await store.sync_bundled(db, "interaction-ai-chat", v2))[0]["status"] == "updated"
    assert "0.5 新增" in (await store.load_skill(db, "group-helper", scope=store.SCOPE_PLUGIN))["content"]

    # 用户编辑：保存为覆盖层
    edited = BUNDLED_V2 + "\n管理员自己的补充。\n"
    row = await store.edit_content(db, "group-helper", edited, user_id=1)
    assert row.user_modified is True and store.upstream_changed(row) is False
    with pytest.raises(store.SkillConflict):
        await store.edit_content(db, "group-helper", SKILL_MD, user_id=1)  # 改名被拒绝

    # 插件再升级：不覆盖用户修改，只标记内置有新版本
    v3 = read_bundled_packages(_bundle_dir(tmp_path, BUNDLED_V2 + "\n插件 0.6 再改。\n"))
    assert (await store.sync_bundled(db, "interaction-ai-chat", v3))[0]["status"] == "upstream_changed"
    row = await store.get_installed(db, "group-helper")
    assert store.upstream_changed(row) is True
    loaded = await store.load_skill(db, "group-helper", scope=store.SCOPE_PLUGIN)
    assert "管理员自己的补充" in loaded["content"] and "0.6 再改" not in loaded["content"]
    info_ = await store.upstream_info(db, "group-helper")
    assert "0.6 再改" in info_["upstream_skill_md"] and "+管理员自己的补充。" in info_["diff"]

    kept = await store.acknowledge_upstream(db, "group-helper", user_id=1)
    assert store.upstream_changed(kept) is False and kept.user_modified is True

    restored = await store.restore_default(db, "group-helper", user_id=1)
    assert restored.user_modified is False
    loaded = await store.load_skill(db, "group-helper", scope=store.SCOPE_PLUGIN)
    assert "0.6 再改" in loaded["content"] and "管理员自己的补充" not in loaded["content"]

    # 同名但属于别的来源：不覆盖
    await store.install(db, fetch_paste(SKILL_MD.replace("pdf-helper", "taken")), user_id=1)
    other = read_bundled_packages(_bundle_dir(tmp_path, SKILL_MD.replace("pdf-helper", "taken")))
    assert (await store.sync_bundled(db, "other-plugin", other))[0]["status"] == "skipped_conflict"

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_exc):
            return False

    facade = PluginSkills(plugin_key="interaction-ai-chat", session_factory=_Ctx)
    bundled = await facade.bundled()
    assert [item["name"] for item in bundled] == ["group-helper"]
    await store.set_enabled(db, "group-helper", enabled_plugins=False, user_id=1)
    assert await facade.bundled() == []


@pytest.mark.asyncio
async def test_md_upload_and_edit_rules(db: AsyncSession) -> None:
    user = SimpleNamespace(id=3)
    created = await api.install_agent_skill(
        AgentSkillInstallIn(source="md_upload", skill_md=SKILL_MD, enabled_plugins=True), db, user
    )
    assert created["source_type"] == "md_upload" and created["enabled_plugins"] is True
    from app.schemas.agent_skill import AgentSkillContentIn

    edited = await api.edit_agent_skill("pdf-helper", AgentSkillContentIn(skill_md=SKILL_MD + "\n补充\n"), db, user)
    assert edited["source_type"] == "md_upload"
    updated = await api.update_agent_skill("pdf-helper", AgentSkillUpdateIn(skill_md=SKILL_MD), db, user)
    assert updated["skill"]["source_type"] == "md_upload"
    with pytest.raises(HTTPException) as nb:
        await api.get_agent_skill_upstream("pdf-helper", db, user)
    assert nb.value.status_code == 409
    row = await store.get_installed(db, "pdf-helper")
    row.source_type = "github"
    await db.flush()
    with pytest.raises(HTTPException) as ne:
        await api.edit_agent_skill("pdf-helper", AgentSkillContentIn(skill_md=SKILL_MD), db, user)
    assert ne.value.detail["code"] == "NOT_EDITABLE"


@pytest.mark.asyncio
async def test_agent_tools_progressive_load_and_registration(db: AsyncSession) -> None:
    from app.services.system_agent.context import ToolContext
    from app.services.system_agent.registry import get_registry
    from app.services.system_agent.runtime import INSTALLED_SKILL_TOOL_NAMES
    from app.services.system_agent.tools import agent_skills as tools

    registry = get_registry()
    for name in INSTALLED_SKILL_TOOL_NAMES:
        spec = registry.get(name)
        assert spec is not None and spec.read_only
    for name in ("agent_skills.install", "agent_skills.uninstall", "agent_skills.update", "agent_skills.set_enabled"):
        spec = registry.get(name)
        assert spec is not None and not spec.read_only and spec.min_role == "admin"

    ctx = ToolContext(db=db, channel="web", role="admin", web_user_id=1)
    await store.install(db, fetch_paste(SKILL_MD), user_id=1)
    loaded = await tools.load_skill(ctx, {"name": "pdf-helper"})
    assert loaded["content"].startswith("# PDF Helper") and "不会执行" in loaded["note"]
    missing = await tools.load_skill(ctx, {"name": "nope"})
    assert missing["error"] == "skill_not_found"
    preview = await tools.install_preview(ctx, {"source": "paste", "skill_md": SKILL_MD.replace("pdf-helper", "two")})
    assert preview.arguments["expected_sha256"] and preview.preview["skill"]["name"] == "two"
    result = await tools.install_execute(ctx, preview.arguments)
    assert result["skill"]["name"] == "two"
    tampered = {**preview.arguments, "skill_md": SKILL_MD.replace("pdf-helper", "three")}
    with pytest.raises(ValueError, match="发生了变化"):
        await tools.install_execute(ctx, tampered)
    with pytest.raises(ValueError):
        await tools.install_preview(ctx, {"source": "zip_upload"})
