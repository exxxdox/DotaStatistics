from types import SimpleNamespace
from unittest.mock import Mock

import lib.deepseekapi as deepseekapi


def build_client(response_content: str = "回答") -> Mock:
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=response_content))]
    )
    return client


def test_dota_analysis_uses_flash_thinking_mode(monkeypatch) -> None:
    client = build_client()
    monkeypatch.setattr(deepseekapi, "get_client", lambda: client)

    assert deepseekapi.deepseekDotaAnalyze("比赛数据") == "回答"

    arguments = client.chat.completions.create.call_args.kwargs
    assert arguments["model"] == "deepseek-v4-flash"
    assert arguments["reasoning_effort"] == "high"
    assert arguments["extra_body"] == {"thinking": {"type": "enabled"}}


def test_general_chat_uses_flash_without_thinking(monkeypatch) -> None:
    client = build_client()
    monkeypatch.setattr(deepseekapi, "get_client", lambda: client)
    # 清理跨测试会话，保证系统提示不受先前消息影响。
    deepseekapi.memory.clear()

    assert deepseekapi.deepseekGeneral("你好") == "回答"

    arguments = client.chat.completions.create.call_args.kwargs
    assert arguments["model"] == "deepseek-v4-flash"
    assert "reasoning_effort" not in arguments
    assert arguments["extra_body"] == {"thinking": {"type": "disabled"}}
