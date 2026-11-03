"""Run the release workflow's actual tag conversion without building an image."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import textwrap

import pytest


BASH = os.environ.get("RELEASE_TEST_BASH") or (
    shutil.which("bash") if os.name == "posix" else None
)


@pytest.mark.skipif(not BASH, reason="requires bash; set RELEASE_TEST_BASH on Windows")
@pytest.mark.parametrize("tag", ["版本1", ".v1", "-v1", "v1.2.3", "v" * 200, ""])
def test_release_tag_conversion_produces_valid_docker_tag(tag: str) -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/release-docker-image.yml"
    ).read_text(encoding="utf-8")
    package_step = workflow.split("      - name: Build and package Docker image\n", 1)[1]
    # Execute the real shell fragment so a copied conversion cannot hide a regression.
    script = textwrap.dedent(package_step.split("        run: |\n", 1)[1])
    script = script.split("docker build --platform", 1)[0]
    result = subprocess.run(
        [BASH, "-c", 'PATH="/usr/bin:$PATH"\n' + script + '\nprintf "%s" "$image_ref"'],
        env={
            **{key: os.environ[key] for key in ("PATH", "SystemRoot", "TEMP", "TMP")
               if key in os.environ},
            "GITHUB_REPOSITORY": "owner/DotaStatistics",
            "GITHUB_REF_NAME": tag,
            "GITHUB_SHA": "1234567890abcdef",
            "LC_ALL": "C",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    image_tag = result.stdout.removeprefix("dota:")
    assert re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", image_tag)
    if tag == "v1.2.3":
        assert image_tag == tag
    if not tag:
        assert image_tag == "1234567890ab"
