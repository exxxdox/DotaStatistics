#!/usr/bin/env bash
set -euo pipefail

# 与 deploy.sh 使用相同的物理路径，避免通过符号链接执行时目录不一致。
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(dirname -- "${SCRIPT_PATH}")"

if ! command -v uv >/dev/null 2>&1; then
    # uv 官方安装器将二进制放入当前用户目录，避免污染系统 Python。
    UV_INSTALLER="$(mktemp)"
    curl -LsSf https://astral.sh/uv/install.sh -o "${UV_INSTALLER}"
    sh "${UV_INSTALLER}"
    rm -f "${UV_INSTALLER}"
    export PATH="${HOME}/.local/bin:${PATH}"
fi

cd "${PROJECT_DIR}"
# uv 默认使用 .venv；锁文件保证服务器与开发环境安装相同版本。
uv sync --frozen --no-dev

# 复用独立部署脚本，确保首次安装和后续更新采用相同的 systemd 流程。
bash "${PROJECT_DIR}/deploy.sh"
