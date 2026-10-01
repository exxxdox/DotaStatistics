"""英雄 ID 到名称的解析：中文名来自 hero_name.json，英文名可选注入作为后备。"""

import json
from pathlib import Path


class HeroNameResolver:
    """将 OpenDota 英雄 ID 解析为可展示名称，优先中文名。"""

    def __init__(self, mapping_path: Path) -> None:
        self.mapping_path = mapping_path
        self._zh_names: dict[int, str] = {}
        self._en_names: dict[int, str] = {}

    def load(self) -> None:
        """从 JSON 加载英雄中文名映射。"""
        # 静态映射使用标准库读取，不再为两列名称表安装 Excel 运行依赖。
        data = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data:
            raise ValueError("英雄名称表必须是非空映射")
        if any(not isinstance(name, str) or not name for name in data.values()):
            raise ValueError("英雄名称必须是非空字符串")
        self._zh_names = {int(hero_id): name for hero_id, name in data.items()}

    def set_en_names(self, en_names: dict[int, str]) -> None:
        """注入 OpenDota 提供的英文名，作为中文名缺失时的后备。"""
        self._en_names = en_names

    def resolve(self, hero_id: int) -> str | None:
        zh_name = self._zh_names.get(hero_id)
        if zh_name is not None:
            return zh_name
        return self._en_names.get(hero_id)
