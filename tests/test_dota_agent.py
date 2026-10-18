import json

from service.dota_agent import MAX_TOOL_OUTPUT_CHARS, DotaAgentTools


class StubPlayers:
    def __init__(self) -> None:
        self.records = {"Alice": 123, "Bob": 456}

    def nicknames(self) -> list[str]:
        return list(self.records)

    def get(self, nickname: str) -> int | None:
        return self.records.get(nickname)


class StubApiClient:
    def __init__(self) -> None:
        self.recent_result: str | None = "recent"
        self.today_result: str | None = "today"
        self.recent_arguments: tuple[int, int] | None = None
        self.today_arguments: tuple[int, int] | None = None

    def get_recent_matches(self, dota_id: int, limit: int) -> str | None:
        self.recent_arguments = (dota_id, limit)
        return self.recent_result

    def get_matches_by_date(self, dota_id: int, days: int) -> str | None:
        self.today_arguments = (dota_id, days)
        return self.today_result


def build_tools() -> tuple[DotaAgentTools, StubApiClient]:
    api = StubApiClient()
    tools = DotaAgentTools(StubPlayers(), api)  # type: ignore[arg-type]
    return tools, api


def error_message(result: str) -> str:
    return json.loads(result)["error"]


def test_definitions_expose_only_three_read_only_tools_with_closed_schemas() -> None:
    tools, _ = build_tools()

    definitions = tools.definitions

    assert [item["function"]["name"] for item in definitions] == [
        "list_tracked_players",
        "get_recent_ranked_matches",
        "get_today_matches",
    ]
    assert all("strict" not in item["function"] for item in definitions)
    assert all(
        item["function"]["parameters"]["additionalProperties"] is False
        for item in definitions
    )


def test_lists_only_tracked_nicknames() -> None:
    tools, _ = build_tools()

    result = json.loads(tools.execute("list_tracked_players", {}))

    assert result == {"players": ["Alice", "Bob"]}
    assert 123 not in result["players"]


def test_recent_matches_resolves_bound_nickname_and_limit() -> None:
    tools, api = build_tools()

    assert tools.execute(
        "get_recent_ranked_matches", {"nickname": "Alice", "limit": 3}
    ) == "recent"
    assert api.recent_arguments == (123, 3)


def test_today_matches_resolves_bound_nickname() -> None:
    tools, api = build_tools()

    assert tools.execute("get_today_matches", {"nickname": "Bob"}) == "today"
    assert api.today_arguments == (456, 1)


def test_rejects_unknown_tools_missing_extra_and_non_object_arguments() -> None:
    tools, _ = build_tools()

    assert error_message(tools.execute("delete_player", {})) == "未知工具"
    assert error_message(tools.execute("get_today_matches", {})) == "缺少必填参数"
    assert error_message(
        tools.execute("get_today_matches", {"nickname": "Alice", "url": "x"})
    ) == "包含未允许的参数"
    assert error_message(tools.execute("list_tracked_players", [])) == "参数必须是对象"  # type: ignore[arg-type]
    assert error_message(tools.execute([], {})) == "未知工具"  # type: ignore[arg-type]


def test_rejects_unbound_nickname_and_invalid_argument_types() -> None:
    tools, _ = build_tools()

    assert error_message(
        tools.execute("get_today_matches", {"nickname": "Mallory"})
    ) == "选手未绑定"
    assert error_message(
        tools.execute("get_today_matches", {"nickname": ""})
    ) == "nickname 必须是非空字符串"
    assert error_message(
        tools.execute("get_today_matches", {"nickname": "   "})
    ) == "nickname 必须是非空字符串"
    for limit in (0, 6, True, 1.5, "2"):
        assert error_message(
            tools.execute(
                "get_recent_ranked_matches",
                {"nickname": "Alice", "limit": limit},
            )
        ) == "limit 必须是 1 至 5 的整数"


def test_maps_empty_and_unavailable_results_to_clear_messages() -> None:
    tools, api = build_tools()
    api.recent_result = "\n"
    assert tools.execute(
        "get_recent_ranked_matches", {"nickname": "Alice", "limit": 1}
    ) == "暂时没有查到近期天梯比赛。"

    api.today_result = None
    assert error_message(
        tools.execute("get_today_matches", {"nickname": "Alice"})
    ) == "OpenDota 暂不可用"

    api.today_result = "   "
    assert tools.execute(
        "get_today_matches", {"nickname": "Alice"}
    ) == "该选手近 24 小时没有天梯比赛。"


def test_internal_errors_are_hidden_and_output_is_bounded() -> None:
    tools, api = build_tools()
    api.recent_result = "x" * (MAX_TOOL_OUTPUT_CHARS + 100)
    bounded = tools.execute(
        "get_recent_ranked_matches", {"nickname": "Alice", "limit": 1}
    )
    assert len(bounded) == MAX_TOOL_OUTPUT_CHARS
    assert bounded.endswith("[输出已截断]")

    def fail(_: int, __: int) -> str:
        raise RuntimeError("secret internal detail")

    api.get_matches_by_date = fail
    result = tools.execute("get_today_matches", {"nickname": "Alice"})
    assert error_message(result) == "查询失败"
    assert "secret" not in result
