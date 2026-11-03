"""Verify the container entry point rejects DATA_DIR escaping the data root."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("sh") is None,
    reason="requires POSIX sh",
)
@pytest.mark.parametrize("escape", ["traversal", "symlink"])
def test_data_dir_traversal_is_rejected(tmp_path: Path, escape: str) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped_dir = str(data_root / ".." / "etc")
    if escape == "symlink":
        link = data_root / "escape"
        link.symlink_to(outside, target_is_directory=True)
        escaped_dir = str(link / "child")
    source = Path(__file__).resolve().parents[1] / "docker-entrypoint.sh"
    # 替换受保护根目录后测试不需要写真实 /data，也不会触碰系统目录。
    script = source.read_text(encoding="utf-8").replace(
        'data_root="/data"', f'data_root="{data_root}"'
    )
    script_path = tmp_path / "entrypoint.sh"
    script_path.write_text(script, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_id = bin_dir / "id"
    fake_id.write_text("#!/bin/sh\nprintf '0'\n", encoding="utf-8")
    fake_id.chmod(0o755)
    # 入口脚本使用镜像内的 python 做 realpath；测试转发到当前解释器。
    fake_python = bin_dir / "python"
    fake_python.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        # 字符串前缀检查会放过这个路径；规范化后必须落在根目录之外。
        "DATA_DIR": escaped_dir,
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
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("sh") is None,
    reason="requires POSIX sh",
)
@pytest.mark.parametrize("nested", [False, True])
def test_new_data_dir_repairs_only_new_parents(tmp_path: Path, nested: bool) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    unrelated = data_root / "unrelated"
    unrelated.mkdir(mode=0o755)
    unrelated_mode = unrelated.stat().st_mode
    target = data_root / "new" / "child" if nested else data_root
    log = tmp_path / "ownership"
    log.write_text("", encoding="utf-8")
    mode_log = tmp_path / "modes"
    mode_log.write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def executable(name: str, content: str) -> None:
        path = bin_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    executable("id", "#!/bin/sh\nprintf '0'\n")
    executable("python", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    # 模拟 chown 和 bot 的目录遍历权限，不要求测试进程拥有 root 或真实 bot 用户。
    executable("chown", '#!/bin/sh\nfor path do :; done\nprintf "%s\\n" "$path" >> "$OWNER_LOG"\n')
    executable("chmod", '#!/bin/sh\nprintf "%s\\n" "$*" >> "$MODE_LOG"\nexec /bin/chmod "$@"\n')
    executable("su-exec", f"""#!{sys.executable}
import os
from pathlib import Path
import sys
args = sys.argv[2:]
if args[0] == "test":
    target = Path(args[-1])
    root = Path(os.environ["DATA_ROOT"])
    repaired = Path(os.environ["OWNER_LOG"]).read_text().splitlines()
    while target != root:
        if str(target) not in repaired:
            sys.exit(1)
        target = target.parent
    sys.exit(0)
os.execvp(args[0], args)
""")
    script = (Path(__file__).resolve().parents[1] / "docker-entrypoint.sh").read_text(encoding="utf-8")
    script = script.replace('data_root="/data"', f'data_root="{data_root}"')
    script_path = tmp_path / "entrypoint.sh"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        ["sh", str(script_path), "true"],
        env={**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "DATA_DIR": str(target),
             "DATA_ROOT": str(data_root), "OWNER_LOG": str(log), "MODE_LOG": str(mode_log)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    repaired = log.read_text().splitlines()
    assert str(unrelated) not in repaired
    assert unrelated.stat().st_mode == unrelated_mode
    if nested:
        assert str(target.parent) in repaired
        assert str(data_root) not in repaired
        assert f"700 {target.parent}" in mode_log.read_text().splitlines()
