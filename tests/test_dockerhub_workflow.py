"""Verify the Docker Hub release contract without contacting the registry."""

import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/publish-dockerhub.yml"
BASH = os.environ.get("RELEASE_TEST_BASH") or (
    shutil.which("bash") if os.name == "posix" else None
)


def test_dockerhub_workflow_only_publishes_v_tags_after_tests() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'on:\n  push:\n    tags:\n      - "v*"\n' in workflow
    assert "permissions:\n  contents: read\n" in workflow
    assert "DOCKERHUB_USERNAME: ${{ vars.DOCKERHUB_USERNAME }}" in workflow
    assert "DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}" in workflow
    assert workflow.index("uv run --frozen pytest") < workflow.index("docker push")


@pytest.mark.skipif(not BASH, reason="requires bash; set RELEASE_TEST_BASH on Windows")
@pytest.mark.parametrize("failed_command", ["", "build", "login"])
def test_dockerhub_commands_use_stdin_and_never_push_after_failure(failed_command: str) -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: Build and push Docker Hub image\n", 1)[1]
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
            "FAIL_COMMAND": failed_command,
        },
        capture_output=True, text=True, timeout=10,
    )
    commands = result.stdout.splitlines()
    assert commands[0] == "build --platform linux/amd64 --tag exxxdox/pri:dota ."
    if failed_command != "build":
        assert commands[1] == "login --username test-user --password-stdin"
    assert "test-token" not in result.stdout + result.stderr
    if failed_command:
        assert result.returncode != 0
        assert "push exxxdox/pri:dota" not in commands
    else:
        assert result.returncode == 0, result.stderr
        assert commands[-1] == "push exxxdox/pri:dota"
