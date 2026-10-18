import json
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
    client.with_options.return_value = client
    return client


def build_tool_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_client_limits_timeout_and_retries(monkeypatch) -> None:
    client_factory = Mock(return_value=Mock())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(deepseek_api, "OpenAI", client_factory)
    deepseek_api.get_client.cache_clear()

    try:
        assert deepseek_api.get_client() is client_factory.return_value
    finally:
        deepseek_api.get_client.cache_clear()

    client_factory.assert_called_once_with(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        timeout=30.0,
        max_retries=1,
    )


def test_dota_analysis_uses_flash_thinking_mode(monkeypatch) -> None:
    client = build_client()
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)

    assert deepseek_api.deepseek_dota_analyze("比赛数据") == "回答"

    arguments = client.chat.completions.create.call_args.kwargs
    assert arguments["model"] == "deepseek-v4-flash"
    assert arguments["reasoning_effort"] == "high"
    assert arguments["extra_body"] == {"thinking": {"type": "enabled"}}
    assert arguments["max_tokens"] == deepseek_api.ANALYSIS_MAX_OUTPUT_TOKENS
    client.with_options.assert_not_called()


def test_empty_completion_content_returns_empty_string(monkeypatch) -> None:
    client = build_client()
    client.chat.completions.create.return_value.choices[0].message.content = None
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)

    assert deepseek_api.deepseek_dota_analyze("比赛数据") == ""


def test_general_chat_uses_flash_without_thinking(monkeypatch, memory_store) -> None:
    client = build_client()
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)

    assert deepseek_api.deepseek_general("你好", "c2c:user-a") == "回答"

    arguments = client.chat.completions.create.call_args.kwargs
    assert arguments["model"] == "deepseek-v4-flash"
    assert "reasoning_effort" not in arguments
    assert arguments["extra_body"] == {"thinking": {"type": "disabled"}}
    assert arguments["max_tokens"] == deepseek_api.CHAT_MAX_OUTPUT_TOKENS
    client.with_options.assert_not_called()


def test_general_chat_executes_tool_and_returns_final_answer(
    monkeypatch, memory_store
) -> None:
    client = build_client()
    client.chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=None,
            tool_calls=[build_tool_call("call-1", "recent_matches", '{"nickname":"小明"}')],
        ))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="小明最近五场三胜两负", tool_calls=None,
        ))]),
    ]
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    execute_tool = Mock(return_value='{"wins":3,"losses":2}')
    tools = [{
        "type": "function",
        "function": {"name": "recent_matches", "parameters": {"type": "object"}},
    }]

    result = deepseek_api.deepseek_general(
        "小明最近战绩？", "c2c:user-a",
        tool_definitions=tools, execute_tool=execute_tool,
    )

    assert result == "小明最近五场三胜两负"
    execute_tool.assert_called_once_with("recent_matches", {"nickname": "小明"})
    first_request, second_request = [
        call.kwargs for call in client.chat.completions.create.call_args_list
    ]
    assert first_request["tools"] == tools
    assert first_request["tool_choice"] == "auto"
    assert second_request["messages"][-1] == {
        "role": "tool", "tool_call_id": "call-1",
        "content": '{"wins":3,"losses":2}',
    }


def test_general_chat_always_exposes_tools_for_model_intent_detection(
    monkeypatch, memory_store
) -> None:
    client = build_client()
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    tools = [{"type": "function", "function": {"name": "lookup"}}]
    execute_tool = Mock()

    deepseek_api.deepseek_general(
        "你好", "c2c:user-a",
        tool_definitions=tools, execute_tool=execute_tool,
    )
    request = client.chat.completions.create.call_args.kwargs
    assert request["tools"] == tools
    assert request["tool_choice"] == "auto"
    execute_tool.assert_not_called()


@pytest.mark.parametrize("arguments", ["not-json", "[]"])
def test_general_chat_returns_invalid_arguments_as_tool_error(
    monkeypatch, memory_store, arguments: str
) -> None:
    client = build_client()
    client.chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=None,
            tool_calls=[build_tool_call("call-bad", "recent_matches", arguments)],
        ))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="参数有误", tool_calls=None,
        ))]),
    ]
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    execute_tool = Mock()

    assert deepseek_api.deepseek_general(
        "查询Dota战绩", "c2c:user-a",
        tool_definitions=[{"type": "function", "function": {"name": "recent_matches"}}],
        execute_tool=execute_tool,
    ) == "参数有误"

    execute_tool.assert_not_called()
    tool_result = client.chat.completions.create.call_args_list[1].kwargs["messages"][-1]
    assert json.loads(tool_result["content"])["error"]
    assert tool_result["tool_call_id"] == "call-bad"


@pytest.mark.parametrize(
    ("configured_limit", "expected_limit"),
    [(None, 4), ("2", 2)],
)
def test_general_chat_limits_tool_execution_from_environment(
    monkeypatch, memory_store, configured_limit: str | None, expected_limit: int
) -> None:
    if configured_limit is None:
        monkeypatch.delenv("DOTA_AGENT_MAX_TOOL_CALLS", raising=False)
    else:
        monkeypatch.setenv("DOTA_AGENT_MAX_TOOL_CALLS", configured_limit)
    client = build_client()
    calls = [
        build_tool_call(f"call-{index}", "lookup", "{}") for index in range(1_000)
    ]
    client.chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=None, tool_calls=calls,
        ))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="查询完成", tool_calls=None,
        ))]),
    ]
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    execute_tool = Mock(return_value="ok")

    assert deepseek_api.deepseek_general(
        "批量查询Dota比赛", "c2c:user-a",
        tool_definitions=[{"type": "function", "function": {"name": "lookup"}}],
        execute_tool=execute_tool,
    ) == "查询完成"

    assert execute_tool.call_count == expected_limit
    messages = client.chat.completions.create.call_args_list[1].kwargs["messages"]
    tool_results = [message for message in messages if message["role"] == "tool"]
    assert len(tool_results) == expected_limit
    assistant_calls = [
        message for message in messages if message["role"] == "assistant"
    ][0]["tool_calls"]
    assert len(assistant_calls) == expected_limit


def test_tool_call_limit_is_hard_capped(monkeypatch) -> None:
    monkeypatch.setenv("DOTA_AGENT_MAX_TOOL_CALLS", "1000")

    # 环境变量只能下调，不能通过错误配置关闭硬上限。
    assert deepseek_api._max_tool_calls() == deepseek_api.MAX_TOOL_CALLS_CAP


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_tool_call_limit_rejects_invalid_environment(monkeypatch, value: str) -> None:
    monkeypatch.setenv("DOTA_AGENT_MAX_TOOL_CALLS", value)

    with pytest.raises(RuntimeError, match="DOTA_AGENT_MAX_TOOL_CALLS 必须是正整数"):
        deepseek_api._max_tool_calls()


def test_general_chat_rejects_oversized_tool_arguments(
    monkeypatch, memory_store
) -> None:
    client = build_client()
    client.chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=None,
            tool_calls=[build_tool_call("call-large", "lookup", "x" * 2_049)],
        ))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="参数过长", tool_calls=None,
        ))]),
    ]
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    execute_tool = Mock()

    assert deepseek_api.deepseek_general(
        "查询Dota比赛", "c2c:user-a",
        tool_definitions=[{"type": "function", "function": {"name": "lookup"}}],
        execute_tool=execute_tool,
    ) == "参数过长"

    execute_tool.assert_not_called()
    result = client.chat.completions.create.call_args_list[1].kwargs["messages"][-1]
    assert json.loads(result["content"]) == {"error": "工具参数无效"}


def test_general_chat_stops_if_model_ignores_final_tool_choice(
    monkeypatch, memory_store
) -> None:
    client = build_client()
    repeated_call = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=None, tool_calls=[build_tool_call("call-loop", "lookup", "{}")],
    ))])
    client.chat.completions.create.side_effect = [repeated_call] * 4
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    execute_tool = Mock(return_value="ok")

    result = deepseek_api.deepseek_general(
        "循环查询Dota比赛", "c2c:user-a",
        tool_definitions=[{"type": "function", "function": {"name": "lookup"}}],
        execute_tool=execute_tool,
    )

    assert result == "工具调用次数过多，请缩小查询范围。"
    assert client.chat.completions.create.call_count == 4
    assert execute_tool.call_count == 3
    assert client.chat.completions.create.call_args.kwargs["tool_choice"] == "none"
    assert (
        client.chat.completions.create.call_args.kwargs["max_tokens"]
        == deepseek_api.TOOL_FINAL_MAX_OUTPUT_TOKENS
    )


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
    client.with_options.assert_not_called()


def test_permanent_players_survive_history_window_and_use_latest_ids(
    monkeypatch, memory_store, tmp_path
) -> None:
    import json

    from lib.player_repository import PlayerRepository

    players = PlayerRepository(tmp_path / "players.json")
    players.set("小明", 123)
    # 旧聊天记录会被上下文窗口排除，选手资料必须仍来自独立持久仓库。
    memory_store.append("c2c:user-a", "user", "旧聊天绑定：小明123")
    for index in range(25):
        memory_store.append("c2c:user-a", "user", f"普通聊天{index}")
    client = build_client()
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)

    deepseek_api.deepseek_general(
        "已记录哪些选手？", "c2c:user-a", player_bindings=players.bindings
    )
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert json.loads(messages[1]["content"]) == {"tracked_players": {"小明": 123}}
    assert all("旧聊天绑定" not in message["content"] for message in messages)
    assert "小明" not in messages[0]["content"]

    players.set("小明", 789)
    deepseek_api.deepseek_general(
        "最新ID是什么？", "c2c:user-a", player_bindings=players.bindings
    )
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert json.loads(messages[1]["content"]) == {"tracked_players": {"小明": 789}}

    restarted = PlayerRepository(players.file_path)
    deepseek_api.deepseek_command_question(
        "撒情况", "昵称", "", player_bindings=restarted.bindings
    )
    data = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert json.loads(data)["tracked_players"] == {"小明": 789}


def test_general_chat_reads_injected_store_instead_of_global(monkeypatch, tmp_path) -> None:
    injected = ConversationMemory(tmp_path / "injected")
    injected.append("c2c:alice", "user", "来自注入目录的历史")
    global_store = Mock(side_effect=AssertionError("must use injected memory"))
    monkeypatch.setattr(deepseek_api, "get_memory_store", global_store)
    client = build_client()
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    deepseek_api.deepseek_general("继续", "c2c:alice", memory_store=injected)
    global_store.assert_not_called()
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert messages[1] == {"role": "user", "content": "来自注入目录的历史"}
