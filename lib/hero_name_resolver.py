"""英雄 ID 到名称的解析：中文名来自 hero_name.xlsx，英文名可选注入作为后备。"""

from pathlib import Path

import pandas as pd


class HeroNameResolver:
    """将 OpenDota 英雄 ID 解析为可展示名称，优先中文名。"""

    def __init__(self, excel_path: Path) -> None:
        self.excel_path = excel_path
        self._zh_names: dict[int, str] = {}
        self._en_names: dict[int, str] = {}

    def load(self) -> None:
        """从 Excel 加载英雄中文名映射。"""
        frame = pd.read_excel(self.excel_path)
        self._zh_names = {
            int(row["id"]): row["name_zh"]
            for _, row in frame.iterrows()
        }

    def set_en_names(self, en_names: dict[int, str]) -> None:
        """注入 OpenDota 提供的英文名，作为中文名缺失时的后备。"""
        self._en_names = en_names

    def resolve(self, hero_id: int) -> str | None:
        zh_name = self._zh_names.get(hero_id)
        if zh_name is not None:
            return zh_name
        return self._en_names.get(hero_id)
