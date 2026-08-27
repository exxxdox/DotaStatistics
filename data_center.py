"""共享配置与资源路径。选手/英雄映射改由 lib 下的仓库类持有，不再使用全局列表。"""

from pathlib import Path

from botpy import logging

_log = logging.get_logger()
hero_excel_path = Path(f"{Path(__file__).resolve().parent}/res/hero_name.xlsx")
common_id_path = Path(f"{Path(__file__).resolve().parent}/res/name_id.json")
enable_ai = True
