#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${PROJECT_DIR}"

# systemd 默认 PATH 不包含 uv 官方安装目录，因此显式兼容该位置。
if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
elif [[ -x "${HOME}/.local/bin/uv" ]]; then
    UV_BIN="${HOME}/.local/bin/uv"
else
    echo "uv 未安装，请先运行 init.sh" >&2
    exit 1
fi

# 直接由 uv 使用项目的 .venv，无需激活或手动退出虚拟环境。
exec "${UV_BIN}" run --frozen --no-dev python main.py
