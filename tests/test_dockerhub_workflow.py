"""Verify the Docker Hub release contract without contacting the registry."""

import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release-docker-image.yml"
BASH = os.environ.get("RELEASE_TEST_BASH") or (
    shutil.which("bash") if os.name == "posix" else None
)


def test_dockerhub_workflow_only_publishes_v_tags_after_tests() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'on:\n  push:\n    tags:\n      - "v*"\n' in workflow
    assert "permissions:\n  contents: write\n" in workflow
    assert "DOCKERHUB_USERNAME: ${{ vars.DOCKERHUB_USERNAME }}" in workflow
    assert "DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}" in workflow
    assert workflow.index("uv run --frozen pytest") < workflow.index("docker push")
    assert workflow.count("uv run --frozen pytest") == 1
    assert workflow.count("docker build --platform") == 1
    assert 'echo "image_ref=$image_ref" >>"$GITHUB_OUTPUT"' in workflow
    assert "IMAGE_REF: ${{ steps.package.outputs.image_ref }}" in workflow
    assert "docker save" in workflow and "gh release upload" in workflow
    assert not WORKFLOW.with_name("publish-dockerhub.yml").exists()


@pytest.mark.skipif(not BASH, reason="requires bash; set RELEASE_TEST_BASH on Windows")
@pytest.mark.parametrize("failed_command", ["", "tag", "login"])
def test_dockerhub_commands_use_stdin_and_never_push_after_failure(failed_command: str) -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: Push Docker Hub image\n", 1)[1]
    step = step.split("      - name:", 1)[0]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1])
    # Replace only Docker, then run the actual workflow shell to check failure handling.
    fake_docker = '''
docker() {
    printf '%s\\n' "$*"
    if [[ "$1" == login ]]; then
        local token
        token="$(cat)"
        [[ "$token" == test-token ]] || return 80
    fi
    [[ "$1" != "$FAIL_COMMAND" ]]
}
'''
    result = subprocess.run(
        [BASH, "-c", 'PATH="/usr/bin:$PATH"\n' + fake_docker + script],
        env={
            **{key: os.environ[key] for key in ("PATH", "SystemRoot", "TEMP", "TMP")
               if key in os.environ},
            "DOCKERHUB_USERNAME": "test-user",
            "DOCKERHUB_TOKEN": "test-token",
            "IMAGE_REF": "dota:v1.2.3",
            "FAIL_COMMAND": failed_command,
        },
        capture_output=True, text=True, timeout=10,
    )
    commands = result.stdout.splitlines()
    assert commands[0] == "tag dota:v1.2.3 exxxdox/dota-bot:latest"
    if failed_command != "tag":
        assert commands[1] == "login --username test-user --password-stdin"
    assert "test-token" not in result.stdout + result.stderr
    if failed_command:
        assert result.returncode != 0
        assert "push exxxdox/dota-bot:latest" not in commands
    else:
        assert result.returncode == 0, result.stderr
        assert commands[-1] == "push exxxdox/dota-bot:latest"


def test_compose_uses_published_dockerhub_image() -> None:
    compose = (WORKFLOW.parents[2] / "compose.yaml").read_text(encoding="utf-8")
    assert "    image: exxxdox/dota-bot:latest\n" in compose
