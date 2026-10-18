"""追踪选手映射的仓库：从 name_id.json 读写，替代旧的全局 name_id_ref 列表。"""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any


class PlayerRepository:
    """保存并查询昵称到 Dota ID 的映射，读写持久的 JSON 文件。"""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self._records: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._load()

    @staticmethod
    def _secure_permissions(path: Path, mode: int) -> None:
        if os.name == "posix":
            path.chmod(mode)

    def _load(self) -> None:
        if not self.file_path.exists():
            return
        self._secure_permissions(self.file_path.parent, 0o700)
        self._secure_permissions(self.file_path, 0o600)
        data = json.loads(self.file_path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or any(
            not isinstance(record, dict)
            or not isinstance(record.get("nick_name"), str)
            or type(record.get("dota_id")) is not int
            for record in data
        ):
            # 拒绝部分加载，避免下次保存时静默丢弃损坏或未知记录。
            raise ValueError("Player bindings must be a list of valid records")
        self._records = data

    def set(self, nickname: str, dota_id: int) -> None:
        """绑定或更新昵称对应的 Dota ID，并立即写回磁盘。"""
        with self._lock:
            # 在副本上修改并先落盘；保存失败时内存必须继续等于磁盘旧值。
            updated = [dict(record) for record in self._records]
            for record in updated:
                if record["nick_name"] == nickname:
                    record["dota_id"] = dota_id
                    break
            else:
                updated.append({"nick_name": nickname, "dota_id": dota_id})
            self._save(updated)
            self._records = updated

    def get(self, nickname: str) -> int | None:
        with self._lock:
            for record in self._records:
                if record["nick_name"] == nickname:
                    return record["dota_id"]
        return None

    def nicknames(self) -> list[str]:
        with self._lock:
            return [record["nick_name"] for record in self._records]

    def bindings(self) -> dict[str, int]:
        """返回供 AI 常驻资料使用的独立快照，调用方不能修改仓库。"""
        with self._lock:
            bindings: dict[str, int] = {}
            for record in self._records:
                # 旧文件可能有重复昵称，保持与 get 相同的首项优先语义。
                bindings.setdefault(record["nick_name"], record["dota_id"])
            return bindings

    def _save(self, records: list[dict[str, Any]]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._secure_permissions(self.file_path.parent, 0o700)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.file_path.parent,
                prefix=f".{self.file_path.name}.", delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(records, temporary, ensure_ascii=False, indent=4)
                temporary.flush()
                os.fsync(temporary.fileno())
            self._secure_permissions(temporary_path, 0o600)
            # 同目录替换保证崩溃时保留旧文件或得到完整新文件。
            os.replace(temporary_path, self.file_path)
            temporary_path = None
            if os.name == "posix":
                directory = os.open(self.file_path.parent, os.O_RDONLY)
                try:
                    # 目录项也要落盘；个别文件系统不支持目录 fsync，此时替换已完成。
                    os.fsync(directory)
                except OSError:
                    pass
                finally:
                    os.close(directory)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
