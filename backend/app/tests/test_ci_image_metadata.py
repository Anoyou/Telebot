"""Exercise the image tag generator without a registry or credentials."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ci-image-metadata.sh"
REVISION = "a" * 40


def generate(tmp_path: Path, *, version: str, ref: str, ref_type: str, service: str):
    app = tmp_path / "backend" / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    output = tmp_path / "github-output"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "OWNER": "ExampleOwner",
        "SERVICE": service,
        "REVISION": REVISION,
        "REF_NAME": ref,
        "REF_TYPE": ref_type,
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=tmp_path, env=env,
        capture_output=True, text=True, check=False,
    )
    return result, output.read_text(encoding="utf-8") if output.exists() else ""


@pytest.mark.parametrize("service", ["web", "frontend", "updater"])
@pytest.mark.parametrize(
    ("version", "ref", "ref_type", "suffixes"),
    [
        ("0.120.0-beta.4", "Beta", "branch", ["beta"]),
        ("0.120.0", "Beta", "branch", []),
        ("0.120.0", "main", "branch", ["stable"]),
        ("0.120.0-beta.4", "pr-145", "branch", []),
        ("0.120.0", "v0.120.0", "tag", ["0.120.0", "stable"]),
        ("0.120.0-beta.4", "v0.120.0-beta.4", "tag", ["0.120.0-beta.4", "beta"]),
        ("0.120.0-rc.1", "v0.120.0-rc.1", "tag", ["0.120.0-rc.1", "beta"]),
        ("0.120.0-alpha.1", "Beta", "branch", ["beta"]),
    ],
)
def test_image_tags_preserve_release_channels(tmp_path, service, version, ref, ref_type, suffixes):
    result, output = generate(tmp_path, version=version, ref=ref, ref_type=ref_type, service=service)
    assert result.returncode == 0, result.stderr
    image = f"ghcr.io/exampleowner/telepilot-{service}"
    assert output.splitlines() == [
        f"image={image}", "tags<<EOF",
        f"{image}:sha-{REVISION}",
        *(f"{image}:{suffix}" for suffix in suffixes),
        "EOF",
    ]


@pytest.mark.parametrize(
    ("version", "ref", "ref_type"),
    [
        ("0.120.0-beta.4", "main", "branch"),
        ("0.120.0-beta.4", "v0.120.0", "tag"),
        ("0.120.0", "v0.120.1", "tag"),
        ("0.120.0-preview.1", "Beta", "branch"),
        ("0.120.0-preview.1", "v0.120.0-preview.1", "tag"),
        ("invalid", "Beta", "branch"),
    ],
)
def test_invalid_release_never_emits_a_channel_tag(tmp_path, version, ref, ref_type):
    result, output = generate(tmp_path, version=version, ref=ref, ref_type=ref_type, service="web")
    assert result.returncode != 0
    assert not any(line.endswith((":stable", ":beta")) for line in output.splitlines())
