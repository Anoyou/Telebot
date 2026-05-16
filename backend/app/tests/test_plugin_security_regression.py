"""插件系统安全回归测试（阶段 E）。

覆盖：
1. 远程插件安装时 manifest.py 不会被执行
2. installed 插件无法访问 client.session
3. source_url scheme 白名单校验（只允许 https:// 和 git+ssh://）
4. 插件禁用后旧命令不再触发
5. RemotePlugin.enabled=false 时即使 AccountFeature.enabled=true 也不加载
6. SandboxClient 反射防护（__class__, __dict__ 等）
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.db.models.feature import FEATURE_STATE_DISABLED, AccountFeature
from app.services import remote_plugin_service as svc


class TestRemotePluginSecurity:
    """远程插件安全测试。"""

    # ── 1. manifest.py 在安装阶段不执行 ──────────────────────────

    def test_install_phase_does_not_execute_manifest_py(self, monkeypatch, tmp_path):
        """验证 _read_plugin_metadata 绝对不执行 manifest.py。"""
        monkeypatch.setattr(svc.settings, "plugins_installed_dir", str(tmp_path / "installed"))

        plugin_dir = tmp_path / "installed" / "evil"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        # 创建 plugin.json（合法）
        (plugin_dir / "plugin.json").write_text(
            '{"name": "evil", "version": "1.0.0"}',
            encoding="utf-8",
        )

        # 创建恶意 manifest.py（如果被执行会修改全局状态）
        import os as _os

        (plugin_dir / "manifest.py").write_text(
            'import os\n'
            'os.environ["EVIL_VAR"] = "pwned"\n'
            'MANIFEST = None\n',
            encoding="utf-8",
        )

        # 读取元数据（安装阶段）
        meta = svc._read_plugin_metadata(plugin_dir, fallback_name="evil")

        # 验证：manifest.py 不应该被执行
        assert meta.name == "evil"
        assert meta.version == "1.0.0"
        assert "EVIL_VAR" not in _os.environ

    def test_install_phase_rejects_missing_plugin_json(self, tmp_path, monkeypatch):
        """验证缺少 plugin.json 时抛出 InvalidPluginMetadata。"""
        monkeypatch.setattr(svc.settings, "plugins_installed_dir", str(tmp_path / "installed"))

        plugin_dir = tmp_path / "installed" / "bad"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        # 只有 manifest.py，没有 plugin.json
        (plugin_dir / "manifest.py").write_text("MANIFEST = None\n", encoding="utf-8")

        with pytest.raises(svc.InvalidPluginMetadata) as ex:
            svc._read_plugin_metadata(plugin_dir, fallback_name="bad")
        assert ex.value.code == "PLUGIN_JSON_NOT_FOUND"

    def test_runtime_discovery_does_not_execute_installed_by_default(self, monkeypatch, tmp_path):
        """worker 刷新 builtin 注册表时不能顺手执行 installed 插件代码。"""
        import os as _os

        from app.worker.plugins import loader as loader_mod

        monkeypatch.setattr(svc.settings, "plugins_installed_dir", str(tmp_path / "installed"))
        monkeypatch.delenv("EVIL_RUNTIME_DISCOVERY", raising=False)

        plugin_dir = tmp_path / "installed" / "evil_runtime"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "__init__.py").write_text(
            'import os\n'
            'os.environ["EVIL_RUNTIME_DISCOVERY"] = "pwned"\n'
            "PLUGIN_CLASS = None\n"
            "MANIFEST = None\n",
            encoding="utf-8",
        )

        loader_mod.discover_plugins()

        assert "EVIL_RUNTIME_DISCOVERY" not in _os.environ

    def test_remote_plugin_requires_runtime_package_files(self, tmp_path):
        """远程插件必须按新版文档提供完整运行期包结构。"""
        plugin_dir = tmp_path / "installed" / "single_file"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            """
            {
              "name": "single_file",
              "display_name": "单文件插件",
              "version": "1.0.0",
              "entry": "plugin.py",
              "permissions": ["send_message"]
            }
            """,
            encoding="utf-8",
        )
        (plugin_dir / "plugin.py").write_text(
            "from app.worker.plugins.base import Plugin, register\n"
            "@register\n"
            "class SingleFilePlugin(Plugin):\n"
            "    key = 'single_file'\n"
            "    display_name = '单文件插件'\n",
            encoding="utf-8",
        )

        meta = svc._read_plugin_metadata(plugin_dir, fallback_name="single_file")
        with pytest.raises(svc.InvalidPluginMetadata) as ex:
            svc._validate_runtime_plugin_shape(plugin_dir, meta)

        assert ex.value.code == "PLUGIN_RUNTIME_FILES_MISSING"
        assert "manifest.py" in ex.value.message
        assert "__init__.py" in ex.value.message
        assert "REMOTE-PLUGIN-GUIDE" in ex.value.message

    # ── 2. source_url scheme 白名单 ───────────────────────────────

    def test_validate_source_url_rejects_file_scheme(self):
        """file:// scheme 必须被拒绝。"""
        with pytest.raises(svc.InvalidSourceUrl) as ex:
            svc._validate_source_url("file:///etc/passwd")
        assert "https" in ex.value.message.lower() or "BAD_SOURCE_URL" in ex.value.code

    def test_validate_source_url_rejects_http_scheme(self):
        """http:// scheme 必须被拒绝（必须使用 https）。"""
        with pytest.raises(svc.InvalidSourceUrl) as ex:
            svc._validate_source_url("http://example.com/repo.git")
        assert "https" in ex.value.message.lower()

    def test_validate_source_url_allows_https(self):
        """https:// scheme 必须被允许。"""
        svc._validate_source_url("https://github.com/user/repo.git")
        svc._validate_source_url("https://gitlab.com/user/repo")

    def test_validate_source_url_allows_git_ssh(self):
        """git+ssh:// scheme 必须被允许。"""
        svc._validate_source_url("git+ssh://git@github.com/user/repo.git")

    def test_validate_source_url_allows_scp_style(self):
        """git@host:path 格式必须被允许。"""
        svc._validate_source_url("git@github.com:user/repo.git")

    def test_validate_source_url_rejects_empty(self):
        """空 URL 必须被拒绝。"""
        with pytest.raises(svc.InvalidSourceUrl):
            svc._validate_source_url("")
        with pytest.raises(svc.InvalidSourceUrl):
            svc._validate_source_url("   ")

    # ── 3. git clone timeout ─────────────────────────────────────

    def test_run_git_timeout_works(self):
        """验证 _run_git 支持 timeout 参数。"""

        async def _test():
            with pytest.raises(svc.GitOperationFailed) as ex:
                # 使用一个肯定会超时的命令
                await svc._run_git("clone", "--depth", "1", "https://github.com/git/git.git", "/tmp", timeout=0.001)
            # 超时错误码应该是 GIT_TIMEOUT
            assert "TIMEOUT" in ex.value.code or "超时" in ex.value.message

        asyncio.get_event_loop().run_until_complete(_test())


class TestSandboxClientSecurity:
    """SandboxClient 安全测试。"""

    def _make_sandbox(self):
        """创建一个测试用的 SandboxClient。"""
        from app.worker.plugins.sandbox import SandboxClient

        # 创建一个假 client
        class FakeClient:
            def get_me(self):
                return None

            def send_message(self, *args, **kwargs):
                return None

            session = "REAL_SESSION_OBJECT"

        return SandboxClient(FakeClient(), ["send_message"], plugin_key="test")

    def test_sandbox_blocks_session_access(self):
        """installed 插件绝对不能访问 client.session。"""
        sandbox = self._make_sandbox()

        with pytest.raises(PermissionError) as ex:
            _ = sandbox.session
        assert "禁止访问" in str(ex.value) or "session" in str(ex.value)

    def test_sandbox_blocks_class_reflection(self):
        """禁止通过 __class__ 反射获取真实类型。"""
        sandbox = self._make_sandbox()

        with pytest.raises(PermissionError) as ex:
            _ = sandbox.__class__
        assert "禁止访问" in str(ex.value)

    def test_sandbox_blocks_dict_reflection(self):
        """禁止通过 __dict__ 获取真实属性。"""
        sandbox = self._make_sandbox()

        with pytest.raises(PermissionError) as ex:
            _ = sandbox.__dict__
        assert "禁止访问" in str(ex.value)

    @pytest.mark.asyncio
    async def test_plugin_command_handler_receives_sandbox_client(self):
        """插件命令 handler 不能收到 dispatcher 传入的原始 Telethon client。"""
        from app.worker.plugins.base import PluginContext
        from app.worker.plugins.loader import _wrap_cmd
        from app.worker.plugins.sandbox import SandboxClient

        raw_client = SimpleNamespace(session="REAL_SESSION_OBJECT")
        sandbox = SandboxClient(raw_client, [], plugin_key="demo")
        seen = {}

        async def _handler(client, event, args, account_id, ctx):
            seen["client"] = client
            seen["account_id"] = account_id
            seen["ctx"] = ctx

        ctx = PluginContext(account_id=7, feature_key="demo", client=sandbox)
        wrapped = _wrap_cmd(_handler, ctx)
        await wrapped(raw_client, object(), [], 7)

        assert seen["client"] is sandbox
        assert seen["client"] is not raw_client
        assert seen["account_id"] == 7
        assert seen["ctx"] is ctx

    def test_sandbox_blocks_private_attrs(self):
        """禁止访问私有属性。"""
        sandbox = self._make_sandbox()

        for attr in ("_client", "_api", "_state", "_connection"):
            with pytest.raises(PermissionError):
                getattr(sandbox, attr)

    def test_sandbox_allows_declared_permissions(self):
        """manifest 声明的权限必须被允许。"""
        sandbox = self._make_sandbox()

        # send_message 是声明的权限，必须可用
        assert callable(sandbox.send_message)

    def test_sandbox_blocks_undelared_attrs(self):
        """未声明的属性访问必须被拒绝。"""
        sandbox = self._make_sandbox()

        for attr in ("delete_messages", "forward_messages"):
            with pytest.raises(PermissionError):
                getattr(sandbox, attr)

    def test_sandbox_blocks_mtproto_call(self):
        """禁止 raw MTProto 调用。"""
        sandbox = self._make_sandbox()

        with pytest.raises(PermissionError) as ex:
            sandbox("fake_mtproto_call")
        assert "MTProto" in str(ex.value) or "__call__" in str(ex.value)


class TestPluginCommandLifecycle:
    """插件命令生命周期安全测试。"""

    def test_unregister_removes_plugin_command(self):
        """验证 unregister_plugin_command 能正确移除命令。"""
        from app.worker.command import (
            _PLUGIN_COMMANDS,
            register_plugin_command,
            unregister_plugin_command,
        )

        async def dummy_handler(*args, **kwargs):
            pass

        # 注册一个测试命令
        register_plugin_command("test_cmd_lifecycle", dummy_handler, owner_plugin_key="test", generation=1)
        assert "test_cmd_lifecycle" in _PLUGIN_COMMANDS

        # 注销
        unregister_plugin_command("test_cmd_lifecycle", owner_plugin_key="test")
        assert "test_cmd_lifecycle" not in _PLUGIN_COMMANDS

    def test_unregister_with_wrong_owner_keeps_command(self):
        """指定错误的 owner_plugin_key 时不注销命令。"""
        from app.worker.command import (
            _PLUGIN_COMMANDS,
            register_plugin_command,
            unregister_plugin_command,
        )

        async def dummy_handler(*args, **kwargs):
            pass

        # 注册属于 plugin_a 的命令
        register_plugin_command("test_cmd_owner", dummy_handler, owner_plugin_key="plugin_a", generation=1)

        # 用 plugin_b 尝试注销，应该不成功
        unregister_plugin_command("test_cmd_owner", owner_plugin_key="plugin_b")
        assert "test_cmd_owner" in _PLUGIN_COMMANDS

        # 用正确的 owner 注销
        unregister_plugin_command("test_cmd_owner", owner_plugin_key="plugin_a")
        assert "test_cmd_owner" not in _PLUGIN_COMMANDS

    def test_unregister_all_plugin_commands(self):
        """验证 unregister_all_plugin_commands 能注销所有该插件的命令。"""
        from app.worker.command import (
            _PLUGIN_COMMANDS,
            register_plugin_command,
            unregister_all_plugin_commands,
        )

        async def dummy_handler(*args, **kwargs):
            pass

        # 注册属于 test_plugin 的多个命令
        register_plugin_command("cmd1", dummy_handler, owner_plugin_key="test_plugin", generation=1)
        register_plugin_command("cmd2", dummy_handler, owner_plugin_key="test_plugin", generation=1)
        register_plugin_command("cmd3", dummy_handler, owner_plugin_key="other_plugin", generation=1)

        assert len(_PLUGIN_COMMANDS) >= 3

        # 注销所有 test_plugin 的命令
        unregister_all_plugin_commands(owner_plugin_key="test_plugin")

        # test_plugin 的命令应该被移除
        assert "cmd1" not in _PLUGIN_COMMANDS
        assert "cmd2" not in _PLUGIN_COMMANDS
        # other_plugin 的命令应该保留
        assert "cmd3" in _PLUGIN_COMMANDS


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalars(self):
        return _FakeScalars(self._items)


class _FakeRemotePluginDB:
    def __init__(self, *, account_features=None):
        self.remote = SimpleNamespace(name="idiom_chain", enabled=False)
        self.accounts = [1, 2]
        self.account_features = list(account_features or [])
        self.added = []
        self.flush_count = 0

    async def execute(self, stmt):
        text = str(stmt).lower()
        if "remote_plugin" in text:
            return _FakeResult([self.remote])
        if "account_feature" in text:
            return _FakeResult(self.account_features)
        if "from account" in text:
            return _FakeResult(self.accounts)
        return _FakeResult([])

    def add(self, row):
        self.added.append(row)
        if isinstance(row, AccountFeature):
            self.account_features.append(row)

    async def flush(self):
        self.flush_count += 1


class TestRemotePluginEnableFlow:
    """远程插件启用流程测试。"""

    @pytest.mark.asyncio
    async def test_first_enable_creates_account_feature_rows(self, monkeypatch):
        """新远程插件首次启用后，应能直接被现有账号加载。"""

        async def _fail_reload(*_args, **_kwargs):
            raise AssertionError("reload must happen after commit in callers")

        monkeypatch.setattr(svc, "_trigger_reload", _fail_reload)
        db = _FakeRemotePluginDB()

        row = await svc.enable(db, "idiom_chain", bootstrap_accounts=True)

        assert row.enabled is True
        assert [(af.account_id, af.feature_key, af.enabled, af.state) for af in db.added] == [
            (1, "idiom_chain", True, FEATURE_STATE_DISABLED),
            (2, "idiom_chain", True, FEATURE_STATE_DISABLED),
        ]

    @pytest.mark.asyncio
    async def test_enable_preserves_existing_account_choices(self):
        """已有账号级记录时，不用全局启用覆盖用户选择。"""
        existing = AccountFeature(
            account_id=1,
            feature_key="idiom_chain",
            enabled=False,
            state=FEATURE_STATE_DISABLED,
        )
        db = _FakeRemotePluginDB(account_features=[existing])

        await svc.enable(db, "idiom_chain", bootstrap_accounts=True)

        assert db.added == []
        assert existing.enabled is False

    @pytest.mark.asyncio
    async def test_internal_enable_does_not_bootstrap_accounts_by_default(self):
        """账号级入口只打开全局开关，不应顺带启用所有账号。"""
        db = _FakeRemotePluginDB()

        await svc.enable(db, "idiom_chain")

        assert db.remote.enabled is True
        assert db.added == []


class TestPluginMetadataSchema:
    """PluginMetadataSchema Pydantic 校验测试。"""

    def test_valid_plugin_json(self):
        """合法的 plugin.json 应该通过校验。"""
        from app.services.remote_plugin_service import PluginMetadataSchema

        data = {
            "name": "my_plugin",
            "display_name": "My Plugin",
            "version": "1.0.0",
            "author": "Test Author",
            "permissions": ["send_message"],
        }
        schema = PluginMetadataSchema(**data)
        assert schema.name == "my_plugin"
        assert schema.version == "1.0.0"

    def test_key_as_fallback(self):
        """没有 name 时，key 作为备选。"""
        from app.services.remote_plugin_service import PluginMetadataSchema

        data = {"key": "fallback_key", "version": "1.0.0"}
        schema = PluginMetadataSchema(**data)
        assert schema.name == "fallback_key"

    def test_rejects_path_traversal_in_name(self):
        """name 中包含路径穿越字符必须被拒绝。"""
        from pydantic import ValidationError

        from app.services.remote_plugin_service import PluginMetadataSchema

        for bad_name in ("../etc", "foo/bar", "..", "foo\\bar", "foo\0bar"):
            with pytest.raises(ValidationError):
                PluginMetadataSchema(name=bad_name, version="1.0.0")

    def test_rejects_invalid_version(self):
        """版本号格式不正确必须被拒绝。"""
        from pydantic import ValidationError

        from app.services.remote_plugin_service import PluginMetadataSchema

        with pytest.raises(ValidationError):
            PluginMetadataSchema(name="test", version="latest")

    def test_author_length_limit(self):
        """author 字段长度限制。"""
        from pydantic import ValidationError

        from app.services.remote_plugin_service import PluginMetadataSchema

        with pytest.raises(ValidationError):
            PluginMetadataSchema(name="test", version="1.0.0", author="x" * 300)
