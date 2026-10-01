from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import lib.deepseek_api as deepseek_api
from lib.conversation_memory import ConversationMemory


@pytest.fixture
def memory_store(tmp_path, monkeypatch) -> ConversationMemory:
    store = ConversationMemory(tmp_path / "conversations")
    monkeypatch.setattr(deepseek_api, "get_memory_store", lambda: store)
    return store


def build_client(response_content: str = "回答") -> Mock:
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=response_content))]
    )
    return client


def test_dota_analysis_uses_flash_thinking_mode(monkeypatch) -> None:
    client = build_client()
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)

    assert deepseek_api.deepseek_dota_analyze("比赛数据") == "回答"

    arguments = client.chat.completions.create.call_args.kwargs
    assert arguments["model"] == "deepseek-v4-flash"
    assert arguments["reasoning_effort"] == "high"
    assert arguments["extra_body"] == {"thinking": {"type": "enabled"}}


def test_empty_completion_content_returns_empty_string(monkeypatch) -> None:
    client = build_client()
    client.chat.completions.create.return_value.choices[0].message.content = None
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)

    assert deepseek_api.deepseek_dota_analyze("比赛数据") == ""


def test_hero_recommendations_use_stats_and_flash_thinking(monkeypatch) -> None:
    client = build_client("1号位：敌法师")
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)

    assert deepseek_api.deepseek_hero_recommendations("英雄候选数据") == "1号位：敌法师"

    arguments = client.chat.completions.create.call_args.kwargs
    assert arguments["model"] == "deepseek-v4-flash"
    assert arguments["reasoning_effort"] == "high"
    assert arguments["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "1至5号位" in arguments["messages"][0]["content"]
    assert arguments["messages"][1] == {
        "role": "user",
        "content": "英雄候选数据",
    }


def test_general_chat_uses_flash_without_thinking(monkeypatch, memory_store) -> None:
    client = build_client()
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)

    assert deepseek_api.deepseek_general("你好", "c2c:user-a") == "回答"

    arguments = client.chat.completions.create.call_args.kwargs
    assert arguments["model"] == "deepseek-v4-flash"
    assert "reasoning_effort" not in arguments
    assert arguments["extra_body"] == {"thinking": {"type": "disabled"}}


def test_general_chat_memory_is_isolated_by_conversation(monkeypatch, memory_store) -> None:
    client = build_client()
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    memory_store.append("c2c:user-a", "user", "用户甲的私密内容")
    memory_store.append("c2c:user-a", "assistant", "用户甲的回复")

    deepseek_api.deepseek_general("用户甲的私密内容", "c2c:user-a")
    deepseek_api.deepseek_general("用户乙的问题", "c2c:user-b")

    second_messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert all("用户甲" not in message["content"] for message in second_messages)


def test_general_chat_reloads_both_roles_after_restart(monkeypatch, memory_store) -> None:
    memory_store.append("c2c:user-a", "user", "我喜欢玩辅助")
    memory_store.append("c2c:user-a", "assistant", "可以试试巫医")
    current_id = memory_store.append("c2c:user-a", "user", "刚才推荐什么？")
    # 新存储对象模拟重启，当前已保存的输入不能重复进入历史。
    restarted = ConversationMemory(memory_store.root)
    monkeypatch.setattr(deepseek_api, "get_memory_store", lambda: restarted)
    client = build_client()
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)

    deepseek_api.deepseek_general("刚才推荐什么？", "c2c:user-a", current_id)

    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert messages[1:] == [
        {"role": "user", "content": "我喜欢玩辅助"},
        {"role": "assistant", "content": "可以试试巫医"},
        {"role": "user", "content": "刚才推荐什么？"},
    ]
    assert "我喜欢玩辅助" not in messages[0]["content"]


def test_parameter_question_uses_short_nonthinking_request_without_history(monkeypatch) -> None:
    import json

    client = build_client("请直接发一下 dotaId，可以吗？")
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    memory = Mock()
    monkeypatch.setattr(deepseek_api, "get_memory_store", memory)

    assert "dotaId" in deepseek_api.deepseek_command_question(
        "追踪术", "dotaId", "dotaId 必须是正整数。"
    )

    arguments = client.chat.completions.create.call_args.kwargs
    assert arguments["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in arguments
    assert len(arguments["messages"]) == 2
    assert json.loads(arguments["messages"][1]["content"]) == {
        "command": "追踪术", "field": "dotaId", "reason": "dotaId 必须是正整数。"
    }
    memory.assert_not_called()
