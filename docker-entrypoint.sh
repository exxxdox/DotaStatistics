#!/bin/sh
set -eu

data_root="/data"
data_dir="${DATA_DIR:-$data_root}"

# 新文件默认只允许 bot 访问，避免持久化数据被同容器其他用户读取。
umask 077

if [ "$(id -u)" -eq 0 ]; then
    # 先消解 .. 和符号链接再校验边界，避免 /data/../etc 通过字符串前缀匹配。
    # Alpine 的 busybox realpath 不支持 -m，改用镜像内自带的 Python；-I 隔离工作目录模块。
    if ! data_dir="$(python -I -c '
import os, sys
print(os.path.realpath(sys.argv[1]))
' "$data_dir" 2>/dev/null)"; then
        echo "error: cannot resolve DATA_DIR" >&2
        exit 1
    fi
    case "$data_dir" in
        "$data_root"|"$data_root"/*) ;;
        *)
            echo "error: DATA_DIR must stay within $data_root" >&2
            exit 1
            ;;
    esac

    # 只记录尚不存在的路径段，避免嵌套目录的 root:0700 父目录阻断 bot 遍历。
    existing_parent="$data_dir"
    while [ ! -d "$existing_parent" ] && [ "$existing_parent" != "$data_root" ]; do
        existing_parent="${existing_parent%/*}"
    done
    mkdir -p "$data_dir"
    if [ "$existing_parent" != "$data_dir" ]; then
        created_parent="${data_dir%/*}"
        while [ "$created_parent" != "$existing_parent" ] && [ "$created_parent" != "$data_root" ]; do
            chown -h bot:bot "$created_parent"
            chmod 700 "$created_parent"
            created_parent="${created_parent%/*}"
        done
    fi

    needs_repair=0
    if ! su-exec bot test -r "$data_dir" \
        || ! su-exec bot test -w "$data_dir" \
        || ! su-exec bot test -x "$data_dir"; then
        needs_repair=1
    elif find "$data_dir" \( \
        ! -user bot -o ! -group bot \
        -o -type d ! -perm 700 \
        -o -type f ! -perm 600 \
        \) -print -quit \
        | grep -q .; then
        needs_repair=1
    fi

    if [ "$needs_repair" -eq 1 ]; then
        # 不跟随导入的符号链接，避免 root 修权越过持久化目录边界。
        chown -hR bot:bot "$data_dir"
        find "$data_dir" -type d -exec chmod 700 {} +
        find "$data_dir" -type f -exec chmod 600 {} +
    fi

    if ! su-exec bot test -r "$data_dir" \
        || ! su-exec bot test -w "$data_dir" \
        || ! su-exec bot test -x "$data_dir"; then
        echo "error: bot user cannot access $data_dir" >&2
        exit 1
    fi

    exec su-exec bot "$@"
fi

exec "$@"
