"""Exercise the real iOS contract shortcut shell, including planner precedence."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.ci_test_plan import PLANNER_INPUTS, requires_full  # noqa: E402

OPENAPI = "openapi/telepilot.openapi.json"
COPY = "ios/TelePilotCore/Sources/TelePilotCore/openapi.json"


def run_contract_shortcut(tmp_path, paths, sync_ok=True):
    workflow = (ROOT / ".github/workflows/ios.yml").read_text()
    start = workflow.index('            if [ "$COMPLETE" = "true" ]')
    end = workflow.index('\n          fi\n          echo "$REASON"', start)
    shell = textwrap.dedent(workflow[start:end])
    output = tmp_path / "output"
    calls = tmp_path / "sync-calls"
    # Only stub contract generation checks. The workflow's conditions and any
    # planner invocation execute as written, using the current Python interpreter.
    setup = r'''
set -euo pipefail
SKIP=false; UI_SKIP=false; REASON="完整执行"; COMPLETE=true
python3() {
  if [[ "$1" == *sync-*.py ]]; then
    echo "$1" >> "$SYNC_CALLS"
    return "$SYNC_STATUS"
  fi
  "$TEST_PYTHON" "$@"
}
'''
    result = subprocess.run(
        ["bash", "-c", setup + shell + '\nprintf "%s %s\\n" "$SKIP" "$UI_SKIP" > "$OUTPUT"'],
        cwd=ROOT,
        env={
            **os.environ,
            "FILES": "\n".join(paths),
            "TEST_PYTHON": sys.executable,
            "SYNC_CALLS": str(calls),
            "SYNC_STATUS": "0" if sync_ok else "1",
            "OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return output.read_text().strip(), calls.read_text().splitlines() if calls.exists() else []


@pytest.mark.parametrize("blocker", sorted(PLANNER_INPUTS) + [".github/workflows/ci.yml", "unknown/runtime.cfg"])
@pytest.mark.parametrize("contract", [OPENAPI, COPY])
def test_full_scope_precedes_contract_shortcuts(tmp_path, blocker, contract):
    paths = [blocker, contract]
    assert requires_full(paths)
    state, calls = run_contract_shortcut(tmp_path, paths)
    assert state == "false false", f"Full scope bypassed for {paths}: {state}"
    assert calls == [], "Full scope should not enter contract shortcut checks"


@pytest.mark.parametrize("contract", [OPENAPI, "schemas/plugin-ui-widgets.json", COPY])
@pytest.mark.parametrize("sync_ok", [True, False])
def test_contract_shortcuts_preserve_sync_validation(tmp_path, contract, sync_ok):
    paths = [contract, "docs/README.md"]
    assert not requires_full(paths)
    state, calls = run_contract_shortcut(tmp_path, paths, sync_ok)
    expected = "false false" if not sync_ok else ("false true" if contract == COPY else "true false")
    assert state == expected
    assert len(calls) == (3 if sync_ok else 1)
