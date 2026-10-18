"""Compressed disk history with independent, physically bounded conversations."""

import hashlib
import json
import os
import sqlite3
import tempfile
import zlib
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

DEFAULT_TOTAL_LIMIT_BYTES = 2_000_000_000


class ConversationMemory:
    def __init__(
        self,
        root: Path,
        user_limit_bytes: int = 100_000_000,
        group_limit_bytes: int = 200_000_000,
        total_limit_bytes: int = DEFAULT_TOTAL_LIMIT_BYTES,
    ) -> None:
        if min(user_limit_bytes, group_limit_bytes) < 16_384:
            raise ValueError("Conversation limits must be at least 16384 bytes")
        if total_limit_bytes < 16_384:
            raise ValueError("Conversation total limit must be at least 16384 bytes")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._secure_permissions(self.root, 0o700)
        for database in self.root.glob("*.sqlite3"):
            if database.is_file() and not database.is_symlink():
                self._secure_permissions(database, 0o600)
        self.user_limit_bytes = user_limit_bytes
        self.group_limit_bytes = group_limit_bytes
        self.total_limit_bytes = total_limit_bytes
        # 启动时先回收最旧会话，避免历史版本留下的数据长期超出总预算。
        self._enforce_total_limit(None)

    @staticmethod
    def _secure_permissions(path: Path, mode: int) -> None:
        if os.name == "posix":
            path.chmod(mode)

    def _enforce_total_limit(self, active_path: Path | None) -> None:
        """按最后活动时间淘汰最旧会话，给所有数据库一个总容量预算。"""
        databases = [
            path
            for path in self.root.glob("*.sqlite3")
            if path.is_file() and not path.is_symlink()
        ]
        # 并发写入可能同时淘汰同一文件，进程内也不能假设目录快照始终有效。
        total = 0
        for path in databases:
            try:
                total += path.stat().st_size
            except OSError:
                continue
        if total <= self.total_limit_bytes:
            return
        # 当前正在写入的数据库始终保留；删除失败说明仍有进程使用，跳过本次。
        for path in sorted(
            (path for path in databases if path != active_path),
            key=self._file_mtime_ns,
        ):
            if total <= self.total_limit_bytes:
                break
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError:
                continue
            total -= size

    @staticmethod
    def _file_mtime_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    def _location(self, conversation_id: str) -> tuple[Path, int]:
        kind, separator, identity = conversation_id.partition(":")
        if kind not in ("c2c", "group") or not separator or not identity.strip():
            raise ValueError("Conversation ID must be c2c:<user> or group:<group>")
        digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
        limit = self.user_limit_bytes if kind == "c2c" else self.group_limit_bytes
        return self.root / f"{digest}.sqlite3", limit

    @staticmethod
    def _connect(path: Path, limit: int) -> sqlite3.Connection:
        if os.name == "posix":
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            ConversationMemory._secure_permissions(path, 0o600)
        connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        try:
            # DELETE journal and FULL vacuum avoid an indefinitely growing WAL or free pages.
            connection.execute("PRAGMA page_size = 4096")
            connection.execute("PRAGMA auto_vacuum = FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS messages "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, payload BLOB NOT NULL)"
            )
            ConversationMemory._shrink_to_limit(connection, limit)
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _shrink_to_limit(connection: sqlite3.Connection, limit: int) -> None:
        """让已有数据库也服从当前上限；max_page_count 本身不会缩小旧文件。"""
        max_pages = limit // 4096
        connection.execute(f"PRAGMA max_page_count = {max_pages}")
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        while page_count > max_pages:
            # 分批淘汰最旧记录，直到物理页数降到配置上限以内。
            cursor = connection.execute(
                "DELETE FROM messages WHERE id IN "
                "(SELECT id FROM messages ORDER BY id LIMIT 50)"
            )
            if cursor.rowcount == 0:
                break
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        # 峰值页数曾被压在上限之上时，淘汰后必须重新设置才真正降低当前连接的限制。
        connection.execute(f"PRAGMA max_page_count = {max_pages}")

    def _ensure_fits(
        self, payload: bytes, limit: int, *, pending: bool = False,
        source: sqlite3.Connection, speaker: str = "0" * 64,
    ) -> None:
        # Probe only after SQLITE_FULL: an oversized record must never evict valid history.
        with tempfile.TemporaryDirectory(prefix="memory-probe-", dir=self.root) as root:
            with closing(self._connect(Path(root) / "probe.sqlite3", limit)) as probe:
                try:
                    has_pending = source.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pending'"
                    ).fetchone()
                    if pending or has_pending:
                        probe.execute(self._pending_schema())
                    if has_pending:
                        # Pending states also consume capacity; an oversized write cannot erase history.
                        probe.executemany(
                            "INSERT INTO pending(speaker, payload) VALUES (?, ?)",
                            source.execute("SELECT speaker, payload FROM pending"),
                        )
                    if pending:
                        probe.execute(
                            "INSERT OR REPLACE INTO pending(speaker, payload) VALUES (?, ?)",
                            (speaker, payload),
                        )
                    else:
                        probe.execute("INSERT INTO messages(payload) VALUES (?)", (payload,))
                except sqlite3.OperationalError as error:
                    if error.sqlite_errorcode == sqlite3.SQLITE_FULL:
                        raise ValueError("Compressed record exceeds conversation capacity") from error
                    raise

    @staticmethod
    def _pending_schema() -> str:
        # WITHOUT ROWID avoids a second index for these small per-member states.
        return (
            "CREATE TABLE IF NOT EXISTS pending "
            "(speaker TEXT PRIMARY KEY, payload BLOB NOT NULL) WITHOUT ROWID"
        )

    def _bounded_write(
        self, connection: sqlite3.Connection, sql: str, parameters: tuple,
        payload: bytes, limit: int, *, pending: bool = False,
    ) -> sqlite3.Cursor:
        checked_capacity = False
        while True:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(sql, parameters)
                connection.execute("COMMIT")
                return cursor
            except sqlite3.OperationalError as error:
                # SQLITE_FULL may roll back automatically; ROLLBACK then would mask it.
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                if error.sqlite_errorcode != sqlite3.SQLITE_FULL:
                    raise
                if not checked_capacity:
                    self._ensure_fits(
                        payload, limit, pending=pending, source=connection,
                        speaker=parameters[0] if pending and parameters else "0" * 64,
                    )
                    checked_capacity = True
                # Schema upgrades and state writes use the same oldest-history eviction rule.
                connection.execute("BEGIN IMMEDIATE")
                try:
                    cursor = connection.execute(
                        "DELETE FROM messages WHERE id = (SELECT MIN(id) FROM messages)"
                    )
                    connection.execute("COMMIT")
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
                if cursor.rowcount == 0:
                    raise ValueError("Record cannot fit within conversation capacity") from error

    @staticmethod
    def _speaker_key(speaker_id: str) -> str:
        if not isinstance(speaker_id, str) or not speaker_id.strip():
            raise ValueError("Speaker ID must be nonempty")
        return hashlib.sha256(speaker_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_pending(state: tuple[str, list[str]]) -> None:
        # Validate before any eviction: a malformed follow-up must not erase valid history.
        if (
            not isinstance(state, (tuple, list)) or len(state) != 2
            or state[0] not in ("追踪术", "今儿", "撒情况")
            or not isinstance(state[1], list) or len(state[1]) > 2
            or any(not isinstance(arg, str) or len(arg) > 256 for arg in state[1])
        ):
            raise ValueError("Invalid pending command state")

    def get_pending(
        self, conversation_id: str, speaker_id: str,
    ) -> tuple[str, list[str]] | None:
        path, limit = self._location(conversation_id)
        speaker = self._speaker_key(speaker_id)
        if not path.exists():
            return None
        with closing(self._connect(path, limit)) as connection:
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pending'"
            ).fetchone():
                return None
            row = connection.execute(
                "SELECT payload FROM pending WHERE speaker = ?", (speaker,)
            ).fetchone()
            if row is None:
                return None
            try:
                state = json.loads(zlib.decompress(row[0]).decode("utf-8"))
                self._validate_pending(state)
                return state[0], state[1]
            except (zlib.error, UnicodeError, ValueError, TypeError) as error:
                raise ValueError("Corrupt compressed pending state") from error

    def set_pending(
        self, conversation_id: str, speaker_id: str,
        state: tuple[str, list[str]] | None,
    ) -> None:
        path, limit = self._location(conversation_id)
        speaker = self._speaker_key(speaker_id)
        if state is None:
            if not path.exists():
                return
            with closing(self._connect(path, limit)) as connection:
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pending'"
                ).fetchone():
                    connection.execute("DELETE FROM pending WHERE speaker = ?", (speaker,))
            return
        self._validate_pending(state)
        payload = zlib.compress(json.dumps(state, ensure_ascii=False).encode("utf-8"), 9)
        with closing(self._connect(path, limit)) as connection:
            self._bounded_write(
                connection, self._pending_schema(), (), payload, limit, pending=True,
            )
            self._bounded_write(
                connection,
                "INSERT OR REPLACE INTO pending(speaker, payload) VALUES (?, ?)",
                (speaker, payload), payload, limit, pending=True,
            )
        self._enforce_total_limit(path)

    def append(
        self,
        conversation_id: str,
        role: Literal["user", "assistant"],
        content: str,
        speaker_id: str | None = None,
    ) -> int:
        path, limit = self._location(conversation_id)
        if role not in ("user", "assistant"):
            raise ValueError("Message role must be user or assistant")
        if not isinstance(content, str):
            raise TypeError("Message content must be a string")
        payload = zlib.compress(
            json.dumps(
                {
                    "role": role,
                    "content": content,
                    "speaker_id": speaker_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            level=9,
        )
        with closing(self._connect(path, limit)) as connection:
            cursor = self._bounded_write(
                connection, "INSERT INTO messages(payload) VALUES (?)", (payload,), payload, limit,
            )
            assert cursor.lastrowid is not None
            message_id = cursor.lastrowid
        self._enforce_total_limit(path)
        return message_id

    def clear(self, conversation_id: str) -> None:
        """清空当前会话的记录和所有成员追问，不影响其他会话或选手资料。"""
        path, limit = self._location(conversation_id)
        if not path.exists():
            return
        with closing(self._connect(path, limit)) as connection, connection:
            # 原子删除而非移除数据库文件，避免并发连接写入失联文件；保留递增 ID。
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM messages")
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pending'"
            ).fetchone():
                connection.execute("DELETE FROM pending")

    def read(
        self,
        conversation_id: str,
        # 缩小每次模型请求携带的历史，降低重复输入的 token 消耗。
        max_chars: int = 8_000,
        max_messages: int = 20,
        *,
        before_id: int | None = None,
    ) -> list[dict[str, str]]:
        path, limit = self._location(conversation_id)
        if max_chars < 0 or max_messages < 0:
            raise ValueError("Context limits must be nonnegative")
        if not path.exists() or not max_chars or not max_messages:
            return []
        messages: list[dict[str, str]] = []
        with closing(self._connect(path, limit)) as connection:
            cursor = connection.execute(
                "SELECT payload FROM messages WHERE (? IS NULL OR id < ?) "
                "ORDER BY id DESC LIMIT ?",
                (before_id, before_id, max_messages),
            )
            for (payload,) in cursor:
                try:
                    message = json.loads(zlib.decompress(payload).decode("utf-8"))
                    role, content = message["role"], message["content"]
                    if role not in ("user", "assistant") or not isinstance(content, str):
                        raise ValueError("Invalid message fields")
                except (zlib.error, UnicodeError, ValueError, KeyError, TypeError) as error:
                    raise ValueError("Corrupt compressed conversation history") from error
                # Keep the latest text when a single reply exceeds the model's context budget.
                content = content[-max_chars:]
                messages.append({"role": role, "content": content})
                max_chars -= len(content)
                if not max_chars:
                    break
        messages.reverse()
        return messages


@lru_cache(maxsize=1)
def get_memory_store() -> ConversationMemory:
    # Cache directory configuration only; every operation opens and closes its own connection.
    from data_center import conversation_memory_dir

    return ConversationMemory(Path(conversation_memory_dir))
