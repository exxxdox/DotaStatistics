"""Verify the container entry point rejects DATA_DIR escaping the data root."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(
    os.name != "posix"
    or shutil.which("sh") is None
    or shutil.which("realpath") is None,
    reason="requires POSIX sh and realpath",
)
def test_data_dir_traversal_is_rejected(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "docker-entrypoint.sh"
    # 替换受保护根目录后测试不需要写真实 /data，也不会触碰系统目录。
    script = source.read_text(encoding="utf-8").replace(
        'data_root="/data"', f'data_root="{tmp_path}"'
    )
    script_path = tmp_path / "entrypoint.sh"
    script_path.write_text(script, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_id = bin_dir / "id"
    fake_id.write_text("#!/bin/sh\nprintf '0'\n", encoding="utf-8")
    fake_id.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        # 字符串前缀检查会放过这个路径；规范化后必须落在根目录之外。
        "DATA_DIR": f"{tmp_path}/../etc",
    }

    result = subprocess.run(
        ["sh", str(script_path), "true"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert "must stay within" in result.stderr
