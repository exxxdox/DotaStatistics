from types import SimpleNamespace
from unittest.mock import Mock

import lib.deepseek_api as deepseek_api


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


def test_general_chat_uses_flash_without_thinking(monkeypatch) -> None:
    client = build_client()
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    # 清理跨测试会话，保证系统提示不受先前消息影响。
    deepseek_api.memory.clear()

    assert deepseek_api.deepseek_general("你好") == "回答"

    arguments = client.chat.completions.create.call_args.kwargs
    assert arguments["model"] == "deepseek-v4-flash"
    assert "reasoning_effort" not in arguments
    assert arguments["extra_body"] == {"thinking": {"type": "disabled"}}


def test_general_chat_memory_is_isolated_by_conversation(monkeypatch) -> None:
    client = build_client()
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    deepseek_api.memory.clear()

    deepseek_api.deepseek_general("用户甲的私密内容", "c2c:user-a")
    deepseek_api.deepseek_general("用户乙的问题", "c2c:user-b")

    second_messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert "用户甲的私密内容" not in second_messages[0]["content"]
