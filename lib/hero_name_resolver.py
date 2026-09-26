"""英雄 ID 到名称的解析：中文名来自 hero_name.xlsx，英文名可选注入作为后备。"""

from pathlib import Path

from openpyxl import load_workbook


class HeroNameResolver:
    """将 OpenDota 英雄 ID 解析为可展示名称，优先中文名。"""

    def __init__(self, excel_path: Path) -> None:
        self.excel_path = excel_path
        self._zh_names: dict[int, str] = {}
        self._en_names: dict[int, str] = {}

    def load(self) -> None:
        """从 Excel 加载英雄中文名映射。"""
        # 文件只有两列静态映射，直接使用 openpyxl 避免为简单读取引入 pandas/numpy。
        workbook = load_workbook(self.excel_path, read_only=True, data_only=True)
        try:
            rows = workbook.active.iter_rows(values_only=True)
            headers = next(rows, None)
            if headers is None:
                raise ValueError("英雄名称表为空")
            try:
                id_index = headers.index("id")
                name_index = headers.index("name_zh")
            except ValueError as error:
                raise ValueError("英雄名称表缺少 id 或 name_zh 列") from error

            names: dict[int, str] = {}
            for row in rows:
                hero_id = row[id_index]
                hero_name = row[name_index]
                if hero_id is None or hero_name is None:
                    continue
                names[int(hero_id)] = str(hero_name)
            self._zh_names = names
        finally:
            workbook.close()

    def set_en_names(self, en_names: dict[int, str]) -> None:
        """注入 OpenDota 提供的英文名，作为中文名缺失时的后备。"""
        self._en_names = en_names

    def resolve(self, hero_id: int) -> str | None:
        zh_name = self._zh_names.get(hero_id)
        if zh_name is not None:
            return zh_name
        return self._en_names.get(hero_id)
