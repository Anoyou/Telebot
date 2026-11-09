from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from scripts.ci_baseline import choose_baseline  # noqa: E402
from scripts.ci_change_scope import changed_paths, classify_paths  # noqa: E402
from scripts.ci_run_tests import command_for  # noqa: E402
from scripts.ci_test_plan import (  # noqa: E402
    build_plan,
    fixture_hash,
    ios_dependency_guard,
    ios_ids,
    suite_hash,
    swift_leaf_declarations,
    version_only_paths,
)


def write(root: Path, name: str, text: str = "") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    for name, text in {
        "backend/app/__init__.py": '__version__ = "1.0.0-beta.1"\n',
        "backend/app/services/alpha.py": "from . import helper\ndef result(): return helper.value\n",
        "backend/app/services/helper.py": "value = 1\n",
        "backend/app/services/uncovered.py": "value = 1\n",
        "backend/app/tests/test_alpha.py": "from app.services import alpha\ndef test_alpha(): assert alpha.result() == 1\n",
        "backend/app/tests/test_other.py": "def test_other(): assert True\n",
        "backend/app/tests/test_auth.py": "def test_auth(): assert True\n",
        "backend/app/tests/test_openapi_contract.py": "def test_contract(): assert True\n",
        "backend/app/tests/test_source_guard.py": 'from pathlib import Path\ndef test_source(): Path("app/services/helper.py").read_text()\n',
        "frontend/package.json": '{"version":"1.0.0-beta.1","dependencies":{"x":"1"}}',
        "openapi/telepilot.openapi.json": '{"info":{"version":"1.0.0-beta.1"},"paths":{}}',
    }.items():
        write(tmp_path, name, text)
    mapping = {
        "backend_smoke": ["backend/app/tests/test_auth.py", "backend/app/tests/test_openapi_contract.py"],
        "backend_fixture_hash": fixture_hash(tmp_path),
        "backend_fixture_modules": [],
        "browser_smoke": [],
        "ios_smoke": [],
        "browser": {},
        "ios": {},
    }
    write(tmp_path, "scripts/ci-test-map.json", json.dumps(mapping))
    return tmp_path


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def commit(root):
    git(root, "add", ".")
    git(
        root,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "user.name=CI test",
        "-c",
        "user.email=ci@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return git(root, "rev-parse", "HEAD")


def test_backend_reverse_dependencies_include_smoke_and_source_guards(project):
    item = build_plan(project, ["backend/app/services/helper.py"])["backend"]
    assert item["mode"] == "selected"
    assert set(item["tests"]) == {
        "backend/app/tests/test_alpha.py",
        "backend/app/tests/test_auth.py",
        "backend/app/tests/test_openapi_contract.py",
        "backend/app/tests/test_source_guard.py",
    }
    assert "backend/app/tests/test_other.py" not in item["tests"]


@pytest.mark.parametrize(
    "path",
    [
        "backend/app/config.py",
        "backend/app/models/item.py",
        "backend/app/worker/command.py",
        "backend/pyproject.toml",
        "backend/app/tests/conftest.py",
    ],
)
def test_shared_backend_changes_run_all(project, path):
    write(project, path)
    assert build_plan(project, [path])["backend"]["mode"] == "full"


@pytest.mark.parametrize("path", ["backend/app/services/missing.py", "backend/app/services/uncovered.py"])
def test_unknown_or_deleted_source_runs_all(project, path):
    assert build_plan(project, [path])["backend"]["mode"] == "full"


def test_invalid_python_dependency_graph_runs_all(project):
    write(project, "backend/app/services/alpha.py", "this is invalid (\n")
    assert build_plan(project, ["backend/app/services/helper.py"])["backend"]["mode"] == "full"


def test_changed_fixture_invalidates_incremental_assumption(project):
    write(project, "backend/app/tests/conftest.py", "STATE = 1\n")
    assert build_plan(project, ["backend/app/services/helper.py"])["backend"]["mode"] == "full"


def test_base_graph_keeps_edges_removed_in_head(project):
    git(project, "init", "-q")
    base = commit(project)
    write(project, "backend/app/services/alpha.py", "def result(): return 2\n")
    item = build_plan(project, ["backend/app/services/helper.py"], base=base)["backend"]
    assert item["mode"] == "selected"
    assert "backend/app/tests/test_alpha.py" in item["tests"]


@pytest.mark.parametrize(
    "force,paths",
    [
        (True, ["backend/app/services/helper.py"]),
        (False, []),
        (False, ["scripts/ci-test-map.json"]),
        (False, [".github/workflows/ci.yml"]),
    ],
)
def test_full_modes_never_narrow_a_suite(project, force, paths):
    plan = build_plan(project, paths, force)
    assert all(plan[t]["mode"] == "full" for t in ("backend", "browser", "ios"))
    assert plan["ios"]["shards"] == [1, 2, 3, 4]


def test_bad_mapping_falls_back_to_full(project):
    write(project, "scripts/ci-test-map.json", "{")
    assert build_plan(project, ["backend/app/services/helper.py"])["backend"]["mode"] == "full"


def test_only_proven_version_changes_are_ignored(project):
    git(project, "init", "-q")
    base = commit(project)
    paths = ["backend/app/__init__.py", "frontend/package.json", "openapi/telepilot.openapi.json"]
    for name in paths:
        p = project / name
        p.write_text(p.read_text().replace("1.0.0-beta.1", "1.0.0-beta.2"))
    head = commit(project)
    ignored = version_only_paths(project, paths, base, head)
    assert ignored == set(paths)
    scope = classify_paths(paths, ignored_runtime_paths=ignored)
    assert not scope.backend and not scope.browser and not scope.contract
    assert scope.version and scope.to_outputs()["publish"] == "true"
    assert build_plan(project, paths, ignored=ignored)["backend"]["mode"] == "skip"
    write(project, "frontend/package.json", '{"version":"1.0.0-beta.3","dependencies":{"x":"2"}}')
    write(project, "backend/app/__init__.py", '__version__ = "1.0.0-beta.3"\nSIDE_EFFECT = True\n')
    write(
        project, "openapi/telepilot.openapi.json", '{"info":{"version":"1.0.0-beta.3"},"paths":{"/new":{}}}'
    )
    newer = commit(project)
    assert version_only_paths(project, paths, head, newer) == set()


def test_gateway_and_migrations_only_run_when_relevant():
    api = classify_paths(["backend/app/api/foo.py"])
    assert api.backend and not api.gateway and not api.migrations
    go = classify_paths(["gateway/internal/proxy.go"])
    assert go.gateway and not go.backend and not go.browser
    assert classify_paths(["backend/alembic/versions/new.py"]).migrations
    assert classify_paths(["backend/app/models/item.py"]).migrations
    assert classify_paths(["frontend/pnpm-lock.yaml"]).browser
    assert classify_paths(["frontend/package.json"]).browser
    assert classify_paths(["unrecognized-runtime-file"]).full


def test_newer_failed_or_foreign_runs_do_not_replace_successful_ancestor(project):
    git(project, "init", "-q")
    base = commit(project)
    write(project, "first.txt", "first")
    untested = commit(project)
    write(project, "second.txt", "second")
    head = commit(project)
    runs = [
        None,
        {"head_sha": untested, "conclusion": "failure", "event": "push", "head_branch": "Beta"},
        {"head_sha": untested, "conclusion": "success", "event": "pull_request", "head_branch": "Beta"},
        {"head_sha": base, "conclusion": "success", "event": "push", "head_branch": "Beta"},
    ]
    assert choose_baseline(runs, "Beta", head, project) == base
    assert choose_baseline([], "Beta", head, project) == ""


def test_runner_never_turns_bad_or_empty_selection_into_no_tests(project):
    for selection in (
        {},
        {"mode": "skip", "tests": []},
        {"mode": "selected", "tests": []},
        {"mode": "selected", "tests": ["--ignore=app"]},
        {"mode": "selected", "tests": ["backend/app/tests/../secret.py"]},
    ):
        command, cwd = command_for("backend", selection, root=project)
        assert command == [sys.executable, "-m", "pytest"]
        assert cwd == project / "backend"
    command, _ = command_for(
        "backend", {"mode": "selected", "tests": ["backend/app/tests/test_alpha.py"]}, root=project
    )
    assert command[-1] == "app/tests/test_alpha.py"


def load_shards():
    spec = importlib.util.spec_from_file_location(
        "incremental_ios_shards", REPO_ROOT / "ios/scripts/ui-test-shards.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ios_discovery_matches_selector():
    module = load_shards()
    assert set(module.discover()) == ios_ids(REPO_ROOT)


def test_ios_selected_shards_are_disjoint_and_complete():
    module = load_shards()
    selection = module.discover()[:7]
    shards = module.plan(3, {}, selection)
    flattened = [t for shard in shards for t, _ in shard]
    assert len(flattened) == len(set(flattened)) == len(selection)
    assert set(flattened) == set(selection)
    all_shards = module.plan(4)
    assert {t for shard in all_shards for t, _ in shard} == set(module.discover())


@pytest.mark.parametrize(
    "selection", [[], ["Unknown/testMissing"], ["C/testX", "C/testX"], "invalid", [None]]
)
def test_ios_invalid_selections_cannot_silently_drop_tests(selection):
    with pytest.raises(ValueError):
        load_shards().plan(1, {}, selection)


def test_reviewed_ios_page_keeps_shared_smoke_and_valid_ids(project):
    write(project, "ios/TelePilotApp/LedgerView.swift", "struct LedgerView {}\n")
    write(
        project,
        "ios/TelePilotUITests/Tests.swift",
        """final class ManagementUITests: XCTestCase {
 func testLedgerShowsSummaryAndCharts() {}
}
final class VisualInspectionUITests: XCTestCase {
 func testOperationsAndAssistantWorkspaces() {}
}
final class TelePilotUITests: XCTestCase {
 func testNativeLoginChallenges() {}
 func testUnrelatedFeature() {}
}
""",
    )
    path = project / "scripts/ci-test-map.json"
    mapping = json.loads(path.read_text())
    mapping.update(
        {
            "ios_smoke": ["TelePilotUITests/testNativeLoginChallenges"],
            "ios": {
                "ios/TelePilotApp/LedgerView.swift": [
                    "ManagementUITests/testLedgerShowsSummaryAndCharts",
                    "VisualInspectionUITests/testOperationsAndAssistantWorkspaces",
                ]
            },
            "ios_consumers": {"ios/TelePilotApp/LedgerView.swift": []},
            "ios_suite_hash": suite_hash(project, "ios"),
        }
    )
    path.write_text(json.dumps(mapping))
    plan = build_plan(project, ["ios/TelePilotApp/LedgerView.swift"], target="ios")
    assert plan["ios"]["mode"] == "selected"
    tests = set(plan["ios"]["tests"])
    assert "ManagementUITests/testLedgerShowsSummaryAndCharts" in tests
    assert "VisualInspectionUITests/testOperationsAndAssistantWorkspaces" in tests
    assert "TelePilotUITests/testNativeLoginChallenges" in tests
    assert tests <= ios_ids(project)
    assert len(tests) < len(ios_ids(project))
    write(
        project,
        "ios/TelePilotApp/UnknownConsumer.swift",
        "struct UnknownConsumer { let page = LedgerView() }\n",
    )
    assert build_plan(project, ["ios/TelePilotApp/LedgerView.swift"], target="ios")["ios"]["mode"] == "full"
    (project / "ios/TelePilotApp/UnknownConsumer.swift").unlink()
    with (project / "ios/TelePilotUITests/Tests.swift").open("a") as f:
        f.write("\n// test suite has changed since the mapping was reviewed\n")
    assert build_plan(project, ["ios/TelePilotApp/LedgerView.swift"], target="ios")["ios"]["mode"] == "full"


def test_unknown_ios_page_stays_full():
    assert (
        build_plan(REPO_ROOT, ["ios/TelePilotApp/TelePilotApp.swift"], target="ios")["ios"]["mode"] == "full"
    )


@pytest.mark.parametrize(
    "event,ref,ref_type,base_ref,expected",
    [
        ("push", "Beta", "branch", "", False),
        ("pull_request", "123/merge", "branch", "Beta", False),
        ("pull_request", "123/merge", "branch", "main", True),
        ("push", "main", "branch", "", True),
        ("push", "v1.0.0", "tag", "", True),
        ("workflow_dispatch", "Beta", "branch", "", True),
    ],
)
def test_actual_workflow_full_guards(event, ref, ref_type, base_ref, expected):
    ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text()
    match = re.search(r'(?ms)^          if \[\[ "\$REF_NAME" == "main".*?^          fi$', ci)
    assert match
    env = {
        **os.environ,
        "EVENT_NAME": event,
        "EVENT": event,
        "REF_NAME": ref,
        "REF_TYPE": ref_type,
        "BASE_REF": base_ref,
    }
    script = "args=()\n" + textwrap.dedent(match[0]) + '\nprintf "%s" "${args[*]}"\n'
    result = subprocess.check_output(["bash", "-c", script], env=env, text=True)
    assert ("--force-full" in result) == expected
    ios = (REPO_ROOT / ".github/workflows/ios.yml").read_text()
    match = re.search(r'(?m)^          if \[\[ "\$REF_NAME" == "main".*FORCE_FULL=true; fi$', ios)
    assert match
    result = subprocess.check_output(
        ["bash", "-c", "FORCE_FULL=false\n" + textwrap.dedent(match[0]) + '\nprintf "%s" "$FORCE_FULL"\n'],
        env=env,
        text=True,
    )
    assert (result == "true") == expected


def test_cross_directory_rename_keeps_both_affected_domains(project):
    git(project, "init", "-q")
    base = commit(project)
    moved = project / "frontend/src/moved.py"
    moved.parent.mkdir(parents=True, exist_ok=True)
    (project / "backend/app/services/helper.py").rename(moved)
    head = commit(project)
    paths = changed_paths(project, base, head)
    assert "backend/app/services/helper.py" in paths
    assert "frontend/src/moved.py" in paths
    scope = classify_paths(paths)
    assert scope.backend and scope.frontend
    assert build_plan(project, paths, base=base)["backend"]["mode"] == "full"


@pytest.mark.parametrize(
    "text,safe",
    [
        ('struct Page { var text = "}{"; func render() {} }', True),
        ("struct Page {}\n  func globalHelper() {}", False),
        ("struct Page {}; var sharedState = 1", False),
        ("struct Page {}\n extension String {}", False),
        ("struct Page {", False),
    ],
)
def test_swift_leaf_guard_detects_indented_and_inline_globals(text, safe):
    assert swift_leaf_declarations(text) is safe


def test_ios_navigation_allowlist_does_not_allow_reusing_page_helpers(project):
    page = "ios/TelePilotApp/LedgerView.swift"
    navigation = "ios/TelePilotApp/Navigation.swift"
    write(project, page, "struct LedgerView {}\nstruct LedgerEntry {}\n")
    write(project, navigation, "struct Navigation { let page = LedgerView() }\n")
    mapping = {"ios_consumers": {page: [navigation]}}
    assert ios_dependency_guard(project, [page], mapping, "")
    write(project, navigation, "struct Navigation { let page = LedgerView(); let item = LedgerEntry() }\n")
    assert not ios_dependency_guard(project, [page], mapping, "")
