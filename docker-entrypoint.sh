#!/bin/sh
set -eu

data_dir="${DATA_DIR:-/data}"

# 新文件保持仅容器用户可访问；Windows bind mount 则由宿主机 ACL 决定权限。
umask 077

if [ "$(id -u)" -eq 0 ]; then
    mkdir -p "$data_dir"
    if chown -R bot:bot "$data_dir" 2>/dev/null \
        && chmod 700 "$data_dir" 2>/dev/null; then
        :
    elif su-exec bot test -r "$data_dir" \
        && su-exec bot test -w "$data_dir" \
        && su-exec bot test -x "$data_dir"; then
        # Docker Desktop 的 Windows bind mount 不支持可靠的 chmod/chown。
        export SKIP_DATA_CHMOD=1
        echo "warning: /data uses host-managed permissions" >&2
    else
        echo "error: bot user cannot access $data_dir" >&2
        exit 1
    fi
    exec su-exec bot "$@"
fi

exec "$@"
