#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="dota.service"
SYSTEMD_DIR="/etc/systemd/system"

# 解析脚本自身的真实路径，因此无论从哪个工作目录调用都能定位项目。
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(dirname -- "${SCRIPT_PATH}")"
UNIT_TEMPLATE="${PROJECT_DIR}/res/${SERVICE_NAME}"
UNIT_TARGET="${SYSTEMD_DIR}/${SERVICE_NAME}"

if [[ ${EUID} -ne 0 ]]; then
    echo "部署 systemd 服务需要 root 权限，请使用: sudo bash ${SCRIPT_PATH}" >&2
    exit 1
fi

if [[ ! -f "${UNIT_TEMPLATE}" ]]; then
    echo "找不到 systemd 模板: ${UNIT_TEMPLATE}" >&2
    exit 1
fi

if [[ ! -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
    echo "找不到 .venv/bin/python，请先执行 uv sync --frozen --no-dev" >&2
    exit 1
fi

RENDERED_UNIT="$(mktemp)"
trap 'rm -f "${RENDERED_UNIT}"' EXIT

# 转义 sed 替换字符串中的特殊字符，支持路径包含空格、& 或反斜杠。
ESCAPED_PROJECT_DIR="${PROJECT_DIR//\\/\\\\}"
ESCAPED_PROJECT_DIR="${ESCAPED_PROJECT_DIR//&/\\&}"
ESCAPED_PROJECT_DIR="${ESCAPED_PROJECT_DIR//|/\\|}"
sed "s|__PROJECT_DIR__|${ESCAPED_PROJECT_DIR}|g" \
    "${UNIT_TEMPLATE}" > "${RENDERED_UNIT}"

if grep -q '__PROJECT_DIR__' "${RENDERED_UNIT}"; then
    echo "systemd 模板渲染失败，仍包含 PROJECT_DIR 占位符" >&2
    exit 1
fi

# 提前校验 systemd 最关键的路径约束，避免用无效 unit 覆盖线上文件。
RENDERED_WORKING_DIR="$(sed -n 's/^WorkingDirectory=//p' "${RENDERED_UNIT}")"
if [[ "${RENDERED_WORKING_DIR}" != /* ]]; then
    echo "WorkingDirectory 不是绝对路径: ${RENDERED_WORKING_DIR}" >&2
    exit 1
fi

install -m 0644 "${RENDERED_UNIT}" "${UNIT_TARGET}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
# enable --now 不会重启已运行服务，因此显式 restart 使新 unit 立即生效。
systemctl restart "${SERVICE_NAME}"
systemctl status --no-pager --full "${SERVICE_NAME}"

echo "部署完成: ${UNIT_TARGET}"
echo "项目目录: ${PROJECT_DIR}"
