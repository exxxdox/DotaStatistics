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


data_dir = _resolve_data_dir()
hero_names_path = resource_dir / "hero_name.json"
common_id_path = data_dir / "name_id.json"
hero_stats_cache_path = data_dir / "daily_hero_stats_cache.json"
# 与运行数据共用持久卷，避免容器重建后丢失长期对话。
conversation_memory_dir = data_dir / "conversations"
enable_ai = True
