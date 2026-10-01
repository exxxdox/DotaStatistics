import hashlib
import json
import random
import sqlite3
import string
import zlib
from concurrent.futures import ThreadPoolExecutor

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
