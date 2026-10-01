# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

# Alpine 缩小基础层；构建与运行使用同一 musl 环境，避免二进制依赖不兼容。
FROM python:3.12-alpine AS builder

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./

# 依赖层只受锁文件影响，业务代码变化时可以复用 Docker 构建缓存。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM python:3.12-alpine AS runtime

ENV DATA_DIR=/data \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 固定 UID/GID 便于持久卷保留明确的文件所有权，并避免以 root 运行机器人。
RUN addgroup -g 10001 -S bot \
    && adduser -u 10001 -G bot -S -D -H bot \
    && mkdir --parents /data \
    && chown bot:bot /data

COPY --from=builder /app/.venv /app/.venv
# 依赖装配已拆到 bootstrap，运行镜像必须一起携带，否则入口导入会失败。
COPY --chown=bot:bot main.py qq_bot.py data_center.py bootstrap.py ./
COPY --chown=bot:bot lib ./lib
COPY --chown=bot:bot service ./service
COPY --chown=bot:bot res/hero_name.json ./res/hero_name.json

USER bot

# QQ WebSocket 是主动出站连接，因此镜像不声明或监听入站端口。
CMD ["python", "main.py"]
