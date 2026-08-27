"""追踪选手映射的仓库：从 name_id.json 读写，替代旧的全局 name_id_ref 列表。"""

import json
from pathlib import Path
from typing import Any


class PlayerRepository:
    """保存并查询昵称到 Dota ID 的映射，读写持久的 JSON 文件。"""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self._records: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.file_path.exists():
            return
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 文件损坏时按空仓库启动，避免启动失败；首次写入会重建文件。
            self._records = []
            return
        self._records = [
            record
            for record in data
            if isinstance(record, dict)
            and isinstance(record.get("nick_name"), str)
            and isinstance(record.get("dota_id"), int)
        ]

    def set(self, nickname: str, dota_id: int) -> None:
        """绑定或更新昵称对应的 Dota ID，并立即写回磁盘。"""
        for record in self._records:
            if record["nick_name"] == nickname:
                record["dota_id"] = dota_id
                break
        else:
            self._records.append({"nick_name": nickname, "dota_id": dota_id})
        self._save()

    def get(self, nickname: str) -> int | None:
        for record in self._records:
            if record["nick_name"] == nickname:
                return record["dota_id"]
        return None

    def get_nickname(self, dota_id: int) -> str | None:
        for record in self._records:
            if record["dota_id"] == dota_id:
                return record["nick_name"]
        return None

    def nicknames(self) -> list[str]:
        return [record["nick_name"] for record in self._records]

    def _save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(self._records, ensure_ascii=False, indent=4),
            encoding="utf-8",
        )
