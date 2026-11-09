"""Check publication guards with a local registry-command stand-in."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ci-merge-image.sh"

DOCKER_STUB = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as handle:
    handle.write(json.dumps(args) + "\n")
platforms = [{"platform": {"os": "linux", "architecture": "amd64"}}]
if os.environ["CASE"] != "missing_arm":
    platforms.append({"platform": {"os": "linux", "architecture": "arm64"}})
platforms.append({"platform": {"os": "unknown", "architecture": "unknown"}})
manifest = {"schemaVersion": 2, "manifests": platforms}
if args[:3] == ["buildx", "imagetools", "create"]:
    if "--dry-run" in args:
        print(json.dumps(manifest))
    else:
        assert "--tag" in args
        Path(args[args.index("--metadata-file") + 1]).write_text(json.dumps({
            "containerimage.descriptor": {"digest": "sha256:" + "c" * 64},
        }))
elif args[:3] == ["buildx", "imagetools", "inspect"]:
    print(json.dumps(manifest))
else:
    raise SystemExit(2)
'''


@pytest.mark.skipif(shutil.which("jq") is None, reason="The workflow requires jq on its runner")
@pytest.mark.parametrize(
    "case", ["success", "missing_arm", "missing_artifact", "invalid_digest", "extra_digest", "empty_tags"],
)
def test_only_complete_images_can_update_release_tags(tmp_path, case):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    docker = bin_dir / "docker"
    docker.write_text(DOCKER_STUB, encoding="utf-8")
    docker.chmod(0o755)
    for arch, digest in (("amd64", "a" * 64), ("arm64", "b" * 64)):
        folder = tmp_path / "digests" / f"digests-1-web-{arch}"
        folder.mkdir(parents=True)
        if case == "missing_artifact" and arch == "arm64":
            continue
        name = "bad-digest" if case == "invalid_digest" and arch == "arm64" else digest
        (folder / name).touch()
        if case == "extra_digest" and arch == "arm64":
            (folder / ("d" * 64)).touch()
    tags = "example.invalid/web:sha-test\nexample.invalid/web:beta"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "IMAGE": "example.invalid/web",
        "TAGS": "" if case == "empty_tags" else tags,
        "ARTIFACT_PREFIX": "digests-1-web",
        "CALLS": str(tmp_path / "calls"),
        "CASE": case,
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=False)
    calls_path = tmp_path / "calls"
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
    publications = [
        args for args in calls
        if args[:3] == ["buildx", "imagetools", "create"] and "--dry-run" not in args
    ]
    if case == "success":
        assert result.returncode == 0, result.stderr
        assert len(publications) == 1
        assert [args[args.index("--tag") + 1] for args in publications] == ["example.invalid/web:sha-test"]
        assert "example.invalid/web:beta" in publications[0]
        assert (tmp_path / "output").read_text().strip() == "digest=sha256:" + "c" * 64
    else:
        assert result.returncode != 0
        assert publications == [], "A failed architecture or digest check must not publish tags"
