import hashlib
import json
import os
import random
import sqlite3
import string
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Event

import pytest

from lib.conversation_memory import ConversationMemory


def noise(size: int, seed: int = 0) -> str:
    return "".join(random.Random(seed).choices(string.ascii_letters + string.digits, k=size))


def test_compressed_history_survives_restart_and_isolates_conversations(tmp_path):
    store = ConversationMemory(tmp_path)
    first = store.append("c2c:alice", "user", "你好" * 100, "alice")
    store.append("c2c:alice", "assistant", "记得你")
    store.append("c2c:bob", "user", "独立用户")
    store.append("group:alice", "user", "独立群聊")
    restarted = ConversationMemory(tmp_path)
    assert restarted.read("c2c:alice") == [
        {"role": "user", "content": "你好" * 100},
        {"role": "assistant", "content": "记得你"},
    ]
    assert restarted.read("c2c:alice", before_id=first) == []
    assert restarted.read("c2c:bob")[0]["content"] == "独立用户"
    assert restarted.read("group:alice")[0]["content"] == "独立群聊"
    path = tmp_path / f"{hashlib.sha256(b'c2c:alice').hexdigest()}.sqlite3"
    with sqlite3.connect(path) as connection:
        payload = connection.execute("SELECT payload FROM messages ORDER BY id").fetchone()[0]
        message = json.loads(zlib.decompress(payload))
        assert message["speaker_id"] == "alice"
        assert message["created_at"].endswith("+00:00")
    assert b"alice" not in path.read_bytes()
    assert "你好".encode() not in path.read_bytes()


@pytest.mark.parametrize("conversation,limit", [("c2c:alice", 32768), ("group:room", 49152)])
def test_physical_capacity_discards_oldest(tmp_path, conversation, limit):
    store = ConversationMemory(tmp_path, user_limit_bytes=32768, group_limit_bytes=49152)
    contents = [noise(6000, index) for index in range(20)]
    ids = [store.append(conversation, "user", content) for content in contents]
    path = next(tmp_path.glob("*.sqlite3"))
    assert path.stat().st_size <= limit
    remaining = store.read(conversation, max_chars=200000, max_messages=100)
    assert 0 < len(remaining) < len(contents)
    assert [message["content"] for message in remaining] == contents[-len(remaining):]
    assert ids == sorted(set(ids))
    assert list(tmp_path.iterdir()) == [path]


def test_oversized_record_preserves_history(tmp_path):
    store = ConversationMemory(tmp_path, user_limit_bytes=32768)
    store.append("c2c:alice", "user", "保留原记录")
    with pytest.raises(ValueError, match="exceeds conversation capacity"):
        store.append("c2c:alice", "assistant", noise(100000))
    assert store.read("c2c:alice") == [{"role": "user", "content": "保留原记录"}]
    assert len(list(tmp_path.iterdir())) == 1


def test_concurrent_writes_use_independent_connections(tmp_path):
    store = ConversationMemory(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(lambda i: store.append("group:room", "user", str(i)), range(40)))
    assert len(set(ids)) == 40
    assert {item["content"] for item in store.read("group:room", max_messages=40)} == {str(i) for i in range(40)}


@pytest.mark.parametrize("conversation", ["default", "alice", "c2c:", "group: ", "other:a"])
def test_invalid_conversations_are_rejected(tmp_path, conversation):
    store = ConversationMemory(tmp_path)
    with pytest.raises(ValueError, match="Conversation ID"):
        store.append(conversation, "user", "hello")
    with pytest.raises(ValueError, match="Conversation ID"):
        store.read(conversation)
    assert not list(tmp_path.iterdir())


def test_context_budget_and_before_id(tmp_path):
    store = ConversationMemory(tmp_path)
    store.append("c2c:alice", "user", "12345")
    second = store.append("c2c:alice", "assistant", "abcdefghij")
    assert store.read("c2c:alice", max_chars=4) == [{"role": "assistant", "content": "ghij"}]
    assert store.read("c2c:alice", max_chars=12) == [
        {"role": "user", "content": "45"},
        {"role": "assistant", "content": "abcdefghij"},
    ]
    assert store.read("c2c:alice", before_id=second) == [{"role": "user", "content": "12345"}]
    assert store.read("c2c:alice", max_messages=1) == [{"role": "assistant", "content": "abcdefghij"}]


def test_total_budget_evicts_oldest_conversation(tmp_path):
    store = ConversationMemory(
        tmp_path,
        user_limit_bytes=32768,
        group_limit_bytes=32768,
        total_limit_bytes=1_000_000,
    )
    store.append("c2c:alice", "user", noise(6000, 1))
    store.append("c2c:bob", "user", noise(6000, 2))
    alice = tmp_path / f"{hashlib.sha256(b'c2c:alice').hexdigest()}.sqlite3"
    bob = tmp_path / f"{hashlib.sha256(b'c2c:bob').hexdigest()}.sqlite3"
    # 显式设置活动时间，确保淘汰顺序与“最久未活动”一致。
    os.utime(alice, (1_600_000_000, 1_600_000_000))
    os.utime(bob, (1_600_000_010, 1_600_000_010))
    # 预算恰好等于两个会话，新增第三个会话必须触发最旧文件淘汰。
    limit = alice.stat().st_size + bob.stat().st_size
    bounded = ConversationMemory(
        tmp_path,
        user_limit_bytes=32768,
        group_limit_bytes=32768,
        total_limit_bytes=limit,
    )

    bounded.append("c2c:carol", "user", noise(6000, 3))

    assert not alice.exists()
    assert bob.exists()
    assert sum(path.stat().st_size for path in tmp_path.glob("*.sqlite3")) <= limit


def test_concurrent_total_budget_checks_do_not_evict_extra_conversations(tmp_path, monkeypatch):
    store = ConversationMemory(tmp_path)
    other = ConversationMemory(tmp_path)
    for nickname in ("alice", "bob", "carol"):
        store.append(f"c2c:{nickname}", "user", nickname)
    alice, _ = store._location("c2c:alice")
    bob, _ = store._location("c2c:bob")
    carol, _ = store._location("c2c:carol")
    os.utime(carol, (1, 1))
    store.total_limit_bytes = other.total_limit_bytes = alice.stat().st_size * 2
    deleting_oldest = Barrier(2)
    original_unlink = Path.unlink

    def overlapping_unlink(path, *args, **kwargs):
        if path == carol:
            # 旧实现会在同一份过期容量快照上同时删除；串行实现只会到达一次。
            try:
                deleting_oldest.wait(timeout=1)
            except BrokenBarrierError:
                pass
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", overlapping_unlink)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(store._enforce_total_limit, alice)
        second = executor.submit(other._enforce_total_limit, bob)
        first.result(timeout=5)
        second.result(timeout=5)

    assert alice.exists() and bob.exists()
    assert not carol.exists()


@pytest.mark.parametrize("budget_on_startup", [False, True])
def test_total_budget_waits_for_another_stores_open_read(
    tmp_path, monkeypatch, budget_on_startup,
):
    store = ConversationMemory(tmp_path)
    other = ConversationMemory(tmp_path)
    store.append("c2c:alice", "user", "keep while reading")
    store.append("c2c:bob", "user", "other conversation")
    alice, _ = store._location("c2c:alice")
    bob, _ = store._location("c2c:bob")
    other.total_limit_bytes = bob.stat().st_size
    reading = Event()
    release_read = Event()
    attempted_delete = Event()
    original_decompress = zlib.decompress
    original_unlink = Path.unlink

    def paused_decompress(payload):
        reading.set()
        assert release_read.wait(timeout=5)
        return original_decompress(payload)

    def checked_unlink(path, *args, **kwargs):
        if path == alice:
            attempted_delete.set()
            assert release_read.is_set(), "evicted a database with an open reader"
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(zlib, "decompress", paused_decompress)
    monkeypatch.setattr(Path, "unlink", checked_unlink)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(store.read, "c2c:alice")
        assert reading.wait(timeout=5)
        # 读取连接的 PRAGMA 可能更新 mtime，显式固定启动淘汰的最旧目标。
        os.utime(alice, (1, 1))
        second = (
            executor.submit(
                ConversationMemory, tmp_path, total_limit_bytes=other.total_limit_bytes,
            )
            if budget_on_startup
            else executor.submit(other._enforce_total_limit, bob)
        )
        try:
            attempted_delete.wait(timeout=0.2)
        finally:
            release_read.set()
        assert first.result(timeout=5) == [
            {"role": "user", "content": "keep while reading"}
        ]
        second.result(timeout=5)
    assert not alice.exists()
    assert bob.exists()


def test_lowering_limit_shrinks_existing_database(tmp_path):
    store = ConversationMemory(tmp_path, user_limit_bytes=32768)
    for index in range(8):
        store.append("c2c:alice", "user", noise(3000, index))
    path = next(tmp_path.glob("*.sqlite3"))
    before = path.stat().st_size
    assert before > 16384

    shrunk = ConversationMemory(tmp_path, user_limit_bytes=16384)
    assert shrunk.read("c2c:alice") == []
    assert path.stat().st_size <= 16384


def test_corrupt_record_reports_error(tmp_path):
    store = ConversationMemory(tmp_path)
    store.append("group:room", "user", "hello")
    with sqlite3.connect(next(tmp_path.glob("*.sqlite3"))) as connection:
        connection.execute("UPDATE messages SET payload = ?", (b"broken",))
    with pytest.raises(ValueError, match="Corrupt compressed"):
        store.read("group:room")


def test_pending_survives_restart_is_compressed_and_isolated(tmp_path):
    store = ConversationMemory(tmp_path)
    assert store.get_pending("group:room", "alice") is None
    store.set_pending("group:room", "alice", None)
    assert not list(tmp_path.iterdir())
    store.append("group:room", "user", "保留聊天记录")
    store.set_pending("group:room", "alice", ("追踪术", ["昵称秘密" * 30]))
    store.set_pending("group:room", "bob", ("今儿", []))
    restarted = ConversationMemory(tmp_path)
    assert restarted.get_pending("group:room", "alice") == ("追踪术", ["昵称秘密" * 30])
    assert restarted.get_pending("group:room", "bob") == ("今儿", [])
    assert restarted.get_pending("group:other", "alice") is None
    assert restarted.get_pending("c2c:alice", "alice") is None
    assert restarted.read("group:room") == [{"role": "user", "content": "保留聊天记录"}]
    path = next(tmp_path.glob("*.sqlite3"))
    with sqlite3.connect(path) as connection:
        speaker, payload = connection.execute(
            "SELECT speaker, payload FROM pending WHERE speaker = ?",
            (hashlib.sha256(b"alice").hexdigest(),),
        ).fetchone()
        assert speaker != "alice"
        assert json.loads(zlib.decompress(payload)) == ["追踪术", ["昵称秘密" * 30]]
    assert ("昵称秘密" * 30).encode() not in path.read_bytes()
    restarted.set_pending("group:room", "alice", None)
    assert restarted.get_pending("group:room", "alice") is None
    assert restarted.get_pending("group:room", "bob") == ("今儿", [])


@pytest.mark.parametrize("state", [("错误命令", []), ("今儿", ["a"] * 3), ("今儿", ["a" * 257])])
def test_invalid_pending_preserves_history(tmp_path, state):
    store = ConversationMemory(tmp_path, user_limit_bytes=32768)
    store.append("c2c:alice", "user", "保留原记录")
    with pytest.raises(ValueError, match="Invalid pending"):
        store.set_pending("c2c:alice", "alice", state)
    assert store.read("c2c:alice") == [{"role": "user", "content": "保留原记录"}]


def test_pending_migrates_full_history_and_keeps_physical_limit(tmp_path):
    limit = 32768
    store = ConversationMemory(tmp_path, group_limit_bytes=limit)
    contents = [noise(6000, index) for index in range(12)]
    for content in contents:
        store.append("group:room", "user", content)
    path = next(tmp_path.glob("*.sqlite3"))
    assert path.stat().st_size == limit
    with sqlite3.connect(path) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='pending'").fetchone()
    store.set_pending("group:room", "alice", ("撒情况", ["昵称"]))
    assert store.get_pending("group:room", "alice") == ("撒情况", ["昵称"])
    for content in contents:
        store.append("group:room", "assistant", content)
    assert path.stat().st_size <= limit
    remaining = store.read("group:room", max_chars=200000, max_messages=100)
    assert [message["content"] for message in remaining] == contents[-len(remaining):]
    assert store.get_pending("group:room", "alice") == ("撒情况", ["昵称"])


def test_pending_schema_too_small_preserves_history(tmp_path):
    store = ConversationMemory(tmp_path, user_limit_bytes=16384)
    store.append("c2c:alice", "user", "保留原记录")
    with pytest.raises(ValueError, match="exceeds conversation capacity"):
        store.set_pending("c2c:alice", "alice", ("今儿", []))
    assert store.read("c2c:alice") == [{"role": "user", "content": "保留原记录"}]
    assert next(tmp_path.glob("*.sqlite3")).stat().st_size <= 16384


def test_pending_capacity_is_reserved_when_rejecting_large_message(tmp_path):
    store = ConversationMemory(tmp_path, user_limit_bytes=32768)
    store.append("c2c:alice", "user", "保留原记录")
    store.set_pending("c2c:alice", "alice", ("今儿", []))
    with pytest.raises(ValueError, match="exceeds conversation capacity"):
        store.append("c2c:alice", "assistant", noise(25000))
    assert store.read("c2c:alice") == [{"role": "user", "content": "保留原记录"}]
    assert store.get_pending("c2c:alice", "alice") == ("今儿", [])


def test_clear_is_persistent_isolated_and_keeps_ids_increasing(tmp_path) -> None:
    store = ConversationMemory(tmp_path)
    previous_id = store.append("group:room", "user", "旧历史")
    store.set_pending("group:room", "alice", ("追踪术", []))
    store.set_pending("group:room", "bob", ("撒情况", []))
    store.append("c2c:alice", "user", "独立私聊")
    store.clear("group:room")
    restarted = ConversationMemory(tmp_path)
    assert restarted.read("group:room") == []
    assert restarted.get_pending("group:room", "alice") is None
    assert restarted.get_pending("group:room", "bob") is None
    assert restarted.read("c2c:alice")[0]["content"] == "独立私聊"
    assert restarted.append("group:room", "user", "新历史") > previous_id
    restarted.clear("group:room")
    restarted.clear("group:room")
    restarted.clear("c2c:missing")
    with pytest.raises(ValueError):
        restarted.clear("default")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
def test_storage_permissions_are_private_and_existing_database_is_repaired(tmp_path) -> None:
    tmp_path.chmod(0o755)
    path = tmp_path / "existing.sqlite3"
    path.touch(mode=0o644)

    store = ConversationMemory(tmp_path)
    store.append("c2c:alice", "user", "secret")

    assert tmp_path.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    created = next(item for item in tmp_path.glob("*.sqlite3") if item != path)
    assert created.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("auto_vacuum", [0, 2])
@pytest.mark.parametrize("page_size", [4096, 8192])
def test_legacy_database_migrates_before_evicting_valid_history(tmp_path, auto_vacuum, page_size):
    store = ConversationMemory(tmp_path, user_limit_bytes=32768)
    path, _ = store._location("c2c:legacy")
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA page_size = {page_size}")
        connection.execute(f"PRAGMA auto_vacuum = {auto_vacuum}")
        connection.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, payload BLOB NOT NULL)")
        connection.executemany("INSERT INTO messages(payload) VALUES (?)", [(b"x" * 6000,)] * 20)
        connection.execute("DELETE FROM messages")
        payload = zlib.compress(json.dumps({"role": "user", "content": "keep legacy history"}).encode())
        connection.execute("INSERT INTO messages(payload) VALUES (?)", (payload,))
    connection.close()
    assert path.stat().st_size > 32768

    assert store.read("c2c:legacy") == [{"role": "user", "content": "keep legacy history"}]
    assert path.stat().st_size <= 32768
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == 1
        assert connection.execute("PRAGMA page_size").fetchone()[0] == 4096
    connection.close()
    assert store.append("c2c:legacy", "assistant", "new history") > 20


@pytest.mark.parametrize("payload", [b"broken", zlib.compress(b"{broken"), zlib.compress(b'["unknown", []]')])
def test_corrupt_pending_removes_only_current_speaker_and_preserves_history(tmp_path, payload):
    store = ConversationMemory(tmp_path)
    store.append("group:room", "user", "keep history")
    store.set_pending("group:room", "alice", ("撒情况", []))
    store.set_pending("group:room", "bob", ("追踪术", ["bob"]))
    path, _ = store._location("group:room")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE pending SET payload=? WHERE speaker=?", (payload, store._speaker_key("alice")))
    connection.close()

    assert store.get_pending("group:room", "alice") is None
    assert store.get_pending("group:room", "bob") == ("追踪术", ["bob"])
    assert store.read("group:room") == [{"role": "user", "content": "keep history"}]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 1
    connection.close()


def test_pending_database_errors_are_not_hidden(tmp_path):
    store = ConversationMemory(tmp_path)
    store.set_pending("group:room", "alice", ("撒情况", []))
    path, _ = store._location("group:room")
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE pending RENAME COLUMN payload TO broken")
    connection.close()

    with pytest.raises(sqlite3.OperationalError, match="payload"):
        store.get_pending("group:room", "alice")



@pytest.mark.parametrize("auto_vacuum", [0, 2])
def test_legacy_database_evicts_oldest_and_keeps_recent_history_within_limit(tmp_path, auto_vacuum):
    limit = 65536
    store = ConversationMemory(tmp_path, user_limit_bytes=limit)
    path, _ = store._location("c2c:legacy")
    contents = [noise(800, index) for index in range(100)]
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA auto_vacuum = {auto_vacuum}")
        connection.execute(
            "CREATE TABLE messages "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, payload BLOB NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO messages(payload) VALUES (?)",
            [(zlib.compress(json.dumps({"role": "user", "content": content}).encode()),)
             for content in contents],
        )
    connection.close()
    assert path.stat().st_size > limit

    remaining = store.read("c2c:legacy", max_chars=200000, max_messages=100)

    assert 0 < len(remaining) < len(contents)
    assert [message["content"] for message in remaining] == contents[-len(remaining):]
    assert path.stat().st_size <= limit
    assert store.append("c2c:legacy", "assistant", "new history") > 100
    assert path.stat().st_size <= limit


def test_failed_legacy_vacuum_preserves_original_history(tmp_path, monkeypatch):
    store = ConversationMemory(tmp_path)
    path, _ = store._location("c2c:legacy")
    payload = zlib.compress(json.dumps({"role": "user", "content": "keep history"}).encode())
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE messages "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, payload BLOB NOT NULL)"
        )
        connection.execute("INSERT INTO messages(payload) VALUES (?)", (payload,))
    connection.close()
    original_connect = sqlite3.connect

    class FailingVacuumConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql == "VACUUM":
                raise sqlite3.OperationalError("simulated migration failure")
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        sqlite3, "connect",
        lambda *args, **kwargs: original_connect(*args, **kwargs, factory=FailingVacuumConnection),
    )
    with pytest.raises(sqlite3.OperationalError, match="simulated migration failure"):
        store.read("c2c:legacy")

    with original_connect(path) as connection:
        assert connection.execute("SELECT payload FROM messages").fetchall() == [(payload,)]
    connection.close()
