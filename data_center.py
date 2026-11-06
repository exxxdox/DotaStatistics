"""共享配置与资源路径。"""

import os
from pathlib import Path

from botpy import logging

_log = logging.get_logger()

project_dir = Path(__file__).resolve().parent
resource_dir = project_dir / "res"


def _resolve_data_dir() -> Path:
    """返回运行时数据目录，允许容器把它映射到持久卷。"""
    configured_dir = os.environ.get("DATA_DIR")
    if configured_dir:
        # 提前解析为绝对路径，避免工作目录变化后把数据写到其他位置。
        return Path(configured_dir).expanduser().resolve()
    return project_dir / "data"


def _resolve_operator_ids() -> frozenset[str]:
    """解析允许执行写操作的 QQ 操作者；空集合表示不限制。"""
    return frozenset(
        item.strip()
        for item in os.environ.get("DOTA_OPERATOR_IDS", "").split(",")
        if item.strip()
    )


def _resolve_command_workers() -> int:
    """限定命令并发，避免错误配置放大线程和上游请求消耗。"""
    try:
        workers = int(os.environ.get("DOTA_COMMAND_WORKERS", "4"))
    except ValueError as error:
        raise ValueError("DOTA_COMMAND_WORKERS 必须是 1 至 32 的整数") from error
    if not 1 <= workers <= 32:
        raise ValueError("DOTA_COMMAND_WORKERS 必须是 1 至 32 的整数")
    return workers


def _resolve_ai_workers() -> int:
    """AI 线程池大小，与命令线程池隔离，避免慢 AI 饿死确定性命令。"""
    try:
        workers = int(os.environ.get("DOTA_AI_WORKERS", "2"))
    except ValueError as error:
        raise ValueError("DOTA_AI_WORKERS 必须是 1 至 16 的整数") from error
    if not 1 <= workers <= 16:
        raise ValueError("DOTA_AI_WORKERS 必须是 1 至 16 的整数")
    return workers


data_dir = _resolve_data_dir()
hero_names_path = resource_dir / "hero_name.json"
common_id_path = data_dir / "name_id.json"
hero_stats_cache_path = data_dir / "daily_hero_stats_cache.json"
# 与运行数据共用持久卷，避免容器重建后丢失长期对话。
conversation_memory_dir = data_dir / "conversations"
operator_ids = _resolve_operator_ids()
command_workers = _resolve_command_workers()
ai_workers = _resolve_ai_workers()
enable_ai = True
