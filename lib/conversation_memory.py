"""Compressed disk history with independent, physically bounded conversations."""

import hashlib
import json
import sqlite3
import tempfile
import zlib
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal


class ConversationMemory:
    def __init__(
        self,
        root: Path,
        user_limit_bytes: int = 100_000_000,
        group_limit_bytes: int = 200_000_000,
    ) -> None:
        if min(user_limit_bytes, group_limit_bytes) < 16_384:
            raise ValueError("Conversation limits must be at least 16384 bytes")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.user_limit_bytes = user_limit_bytes
        self.group_limit_bytes = group_limit_bytes

    def _location(self, conversation_id: str) -> tuple[Path, int]:
        kind, separator, identity = conversation_id.partition(":")
        if kind not in ("c2c", "group") or not separator or not identity.strip():
            raise ValueError("Conversation ID must be c2c:<user> or group:<group>")
        digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
        limit = self.user_limit_bytes if kind == "c2c" else self.group_limit_bytes
        return self.root / f"{digest}.sqlite3", limit

    @staticmethod
    def _connect(path: Path, limit: int) -> sqlite3.Connection:
        connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        try:
            # DELETE journal and FULL vacuum avoid an indefinitely growing WAL or free pages.
            connection.execute("PRAGMA page_size = 4096")
            connection.execute("PRAGMA auto_vacuum = FULL")
            connection.execute(f"PRAGMA max_page_count = {limit // 4096}")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS messages "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, payload BLOB NOT NULL)"
            )
            return connection
        except BaseException:
            connection.close()
            raise

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
            return cursor.lastrowid

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
