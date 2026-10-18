#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'HELP'
用法: bash manage.sh <命令>
  init    使用已安装的 uv 同步生产依赖并部署 systemd 服务（需要 root）
  deploy  安装或更新 systemd 服务并重启（需要 root）
  start   前台启动机器人
  help    显示帮助
HELP
}

# 先验证参数，避免误输入触发安装或服务重启。
if [[ $# -eq 0 ]]; then
    usage
    exit 0
fi
if [[ $# -ne 1 ]]; then
    usage >&2
    exit 2
fi
case "$1" in
    help|-h|--help) usage; exit 0 ;;
    init|deploy|start) ;;
    *) echo "未知命令: $1" >&2; usage >&2; exit 2 ;;
esac

# 所有子命令共用真实项目路径，支持从其他目录或符号链接调用。
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(dirname -- "${SCRIPT_PATH}")"
SERVICE_NAME="dota.service"
SERVICE_USER="dota"
SERVICE_GROUP="dota"
DATA_DIR="/var/lib/dota"
SECRETS_FILE="/etc/dota/secrets"
UNIT_TEMPLATE="${PROJECT_DIR}/res/${SERVICE_NAME}"
UNIT_TARGET="/etc/systemd/system/${SERVICE_NAME}"

require_root() {
    if [[ ${EUID} -ne 0 ]]; then
        echo "部署 systemd 服务需要 root 权限，请使用: sudo bash ${SCRIPT_PATH} $1" >&2
        exit 1
    fi
}

find_uv() {
    # systemd 和 sudo 的 PATH 可能不包含 uv 官方安装位置。
    if command -v uv >/dev/null 2>&1; then
        UV_BIN="$(command -v uv)"
    elif [[ -x "${HOME}/.local/bin/uv" ]]; then
        UV_BIN="${HOME}/.local/bin/uv"
    else
        return 1
    fi
}

init() {
    # 安装完成后必须部署服务，先校验权限，避免留下半完成的初始化。
    require_root init
    if ! find_uv; then
        echo "uv 未安装。请先通过系统包管理器安装固定版本的 uv。" >&2
        exit 1
    fi
    cd "${PROJECT_DIR}"
    "${UV_BIN}" sync --frozen --no-dev
    deploy
}

deploy() {
    require_root deploy
    if [[ ! -f "${UNIT_TEMPLATE}" ]]; then
        echo "找不到 systemd 模板: ${UNIT_TEMPLATE}" >&2
        exit 1
    fi

    if [[ ! -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
        echo "找不到 .venv/bin/python，请先执行 uv sync --frozen --no-dev" >&2
        exit 1
    fi

    # 服务账户不允许登录，只拥有运行数据；源码继续由部署账户管理。
    if ! getent group "${SERVICE_GROUP}" >/dev/null; then
        groupadd --system "${SERVICE_GROUP}"
    fi
    if ! getent passwd "${SERVICE_USER}" >/dev/null; then
        useradd --system --gid "${SERVICE_GROUP}" --home-dir "${DATA_DIR}" \
            --shell /usr/sbin/nologin "${SERVICE_USER}"
    fi
    install -d -m 0700 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${DATA_DIR}"
    install -d -m 0750 -o root -g "${SERVICE_GROUP}" "$(dirname -- "${SECRETS_FILE}")"
    if [[ ! -f "${SECRETS_FILE}" ]]; then
        echo "找不到密钥文件: ${SECRETS_FILE}" >&2
        echo "请按 README 创建后重新部署。" >&2
        exit 1
    fi
    chown root:"${SERVICE_GROUP}" "${SECRETS_FILE}"
    chmod 0640 "${SECRETS_FILE}"

    # 在覆盖 unit 前确认服务账户能读取代码和执行虚拟环境。
    if ! runuser -u "${SERVICE_USER}" -- test -r "${PROJECT_DIR}/main.py" \
        || ! runuser -u "${SERVICE_USER}" -- test -x "${PROJECT_DIR}/.venv/bin/python"; then
        echo "${SERVICE_USER} 无法访问项目目录，请把项目放在 /opt 等可遍历路径。" >&2
        exit 1
    fi

    # systemd-analyze 从文件名识别 unit 类型，因此保留 .service 后缀。
    RENDERED_UNIT="$(mktemp --suffix=.service)"
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

    # 先验证临时 unit，避免错误配置覆盖正在运行的服务。
    systemd-analyze verify "${RENDERED_UNIT}"

    install -m 0644 "${RENDERED_UNIT}" "${UNIT_TARGET}"
    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}"
    # enable --now 不会重启已运行服务，因此显式 restart 使新 unit 立即生效。
    systemctl restart "${SERVICE_NAME}"
    systemctl status --no-pager --full "${SERVICE_NAME}"

    echo "部署完成: ${UNIT_TARGET}"
    echo "项目目录: ${PROJECT_DIR}"
}

start() {
    cd "${PROJECT_DIR}"
    if ! find_uv; then
        echo "uv 未安装，请先运行: sudo bash ${SCRIPT_PATH} init" >&2
        exit 1
    fi
    # 前台进程直接替换脚本，让退出码和停止信号传给机器人。
    exec "${UV_BIN}" run --frozen --no-dev python main.py
}

"$1"
