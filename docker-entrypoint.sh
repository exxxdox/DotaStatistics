#!/bin/sh
set -eu

data_root="/data"
data_dir="${DATA_DIR:-$data_root}"

# 新文件默认只允许 bot 访问，避免持久化数据被同容器其他用户读取。
umask 077

if [ "$(id -u)" -eq 0 ]; then
    # 先消解 .. 和符号链接再校验边界，避免 /data/../etc 通过字符串前缀匹配。
    if ! data_dir="$(realpath -m "$data_dir" 2>/dev/null)"; then
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

    mkdir -p "$data_dir"

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
