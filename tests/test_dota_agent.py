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
        self.calls: list[tuple[object, ...]] = []
        self.structured_result: object = {"result": "ok"}

    def get_recent_matches(self, dota_id: int, limit: int) -> str | None:
        self.recent_arguments = (dota_id, limit)
        return self.recent_result

    def get_matches_by_date(self, dota_id: int, days: int) -> str | None:
        self.today_arguments = (dota_id, days)
        return self.today_result

    def get_player_data(
        self,
        account_id: int,
        view: str,
        *,
        days: int,
        limit: int,
    ) -> object:
        self.calls.append(("player", account_id, view, days, limit))
        return self.structured_result

    def get_match_data(self, match_id: int) -> object:
        self.calls.append(("match", match_id))
        return self.structured_result

    def get_hero_data(self, hero_id: int, view: str, *, limit: int) -> object:
        self.calls.append(("hero", hero_id, view, limit))
        return self.structured_result

    def get_team_data(self, team_id: int, view: str, *, limit: int) -> object:
        self.calls.append(("team", team_id, view, limit))
        return self.structured_result

    def get_global_data(self, view: str, *, limit: int) -> object:
        self.calls.append(("global", view, limit))
        return self.structured_result


def build_tools() -> tuple[DotaAgentTools, StubApiClient]:
    api = StubApiClient()
    tools = DotaAgentTools(StubPlayers(), api)  # type: ignore[arg-type]
    return tools, api


def error_message(result: str) -> str:
    return json.loads(result)["error"]


def test_definitions_and_handlers_expose_only_read_only_tools_with_closed_schemas(
) -> None:
    tools, _ = build_tools()

    definitions = tools.definitions

    assert [item["function"]["name"] for item in definitions] == [
        "list_tracked_players",
        "get_recent_ranked_matches",
        "get_today_matches",
        "get_player_data",
        "get_match_data",
        "get_hero_data",
        "get_team_data",
        "get_global_dota_data",
    ]
    assert all("strict" not in item["function"] for item in definitions)
    assert all(
        item["function"]["parameters"]["additionalProperties"] is False
        for item in definitions
    )
    assert all(
        set(item["function"]["parameters"]["properties"])
        == set(item["function"]["parameters"]["required"])
        for item in definitions
    )

    valid_arguments = {
        "list_tracked_players": {},
        "get_recent_ranked_matches": {"nickname": "Alice", "limit": 1},
        "get_today_matches": {"nickname": "Alice"},
        "get_player_data": {
            "nickname": "Alice",
            "view": "profile",
            "days": 30,
            "limit": 5,
        },
        "get_match_data": {"match_id": 1},
        "get_hero_data": {"hero_id": 1, "view": "matchups", "limit": 5},
        "get_team_data": {"team_id": 1, "view": "overview", "limit": 5},
        "get_global_dota_data": {"view": "live_matches", "limit": 5},
    }
    assert all(
        error_message(tools.execute(name, {"unexpected": True}))
        != "未知工具"
        for name in valid_arguments
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


def test_player_views_resolve_bound_nickname_and_route_all_arguments() -> None:
    tools, api = build_tools()

    for view in (
        "profile",
        "win_loss",
        "heroes",
        "peers",
        "totals",
        "counts",
        "ratings",
        "rankings",
    ):
        result = tools.execute(
            "get_player_data",
            {"nickname": "Alice", "view": view, "days": 30, "limit": 7},
        )
        assert json.loads(result) == {"result": "ok"}
        assert api.calls[-1] == ("player", 123, view, 30, 7)


def test_match_route_rejects_bool_and_accepts_positive_id() -> None:
    tools, api = build_tools()

    assert json.loads(tools.execute("get_match_data", {"match_id": 99})) == {
        "result": "ok"
    }
    assert api.calls[-1] == ("match", 99)
    for match_id in (0, -1, True, 1.5, "1"):
        assert error_message(
            tools.execute("get_match_data", {"match_id": match_id})
        ) == "match_id 必须是正整数"


def test_hero_team_and_global_views_route_to_corresponding_client_methods() -> None:
    tools, api = build_tools()

    for view in ("matchups", "durations", "recent_matches", "rankings"):
        assert json.loads(
            tools.execute(
                "get_hero_data", {"hero_id": 2, "view": view, "limit": 6}
            )
        ) == {"result": "ok"}
        assert api.calls[-1] == ("hero", 2, view, 6)

    for view in ("overview", "matches", "players", "heroes"):
        assert json.loads(
            tools.execute(
                "get_team_data", {"team_id": 3, "view": view, "limit": 8}
            )
        ) == {"result": "ok"}
        assert api.calls[-1] == ("team", 3, view, 8)

    for view in (
        "live_matches",
        "pro_matches",
        "public_matches",
        "top_players",
        "hero_stats",
        "pro_players",
    ):
        assert json.loads(
            tools.execute("get_global_dota_data", {"view": view, "limit": 9})
        ) == {"result": "ok"}
        assert api.calls[-1] == ("global", view, 9)


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

    assert error_message(
        tools.execute(
            "get_player_data",
            {
                "nickname": "Mallory",
                "view": "profile",
                "days": 30,
                "limit": 5,
            },
        )
    ) == "选手未绑定"


def test_new_tools_reject_missing_extra_unknown_views_and_numeric_boundaries() -> None:
    tools, _ = build_tools()

    assert error_message(tools.execute("get_player_data", {})) == "缺少必填参数"
    assert error_message(
        tools.execute(
            "get_player_data",
            {
                "nickname": "Alice",
                "view": "profile",
                "days": 30,
                "limit": 5,
                "url": "https://example.invalid",
            },
        )
    ) == "包含未允许的参数"

    invalid_views = [
        (
            "get_player_data",
            {"nickname": "Alice", "view": "matches", "days": 30, "limit": 5},
        ),
        ("get_hero_data", {"hero_id": 1, "view": "players", "limit": 5}),
        ("get_team_data", {"team_id": 1, "view": "rankings", "limit": 5}),
        ("get_global_dota_data", {"view": "explorer", "limit": 5}),
    ]
    for name, arguments in invalid_views:
        assert error_message(tools.execute(name, arguments)) == "未知 view"

    for days in (0, 366, True, 1.5, "30"):
        assert error_message(
            tools.execute(
                "get_player_data",
                {
                    "nickname": "Alice",
                    "view": "profile",
                    "days": days,
                    "limit": 5,
                },
            )
        ) == "days 必须是 1 至 365 的整数"

    for limit in (0, 21, True, 1.5, "5"):
        assert error_message(
            tools.execute(
                "get_global_dota_data", {"view": "live_matches", "limit": limit}
            )
        ) == "limit 必须是 1 至 20 的整数"

    id_tools = (("get_hero_data", "hero_id"), ("get_team_data", "team_id"))
    for tool_name, id_name in id_tools:
        for value in (0, -1, True, 1.5, "1"):
            view = "matchups" if id_name == "hero_id" else "overview"
            assert error_message(
                tools.execute(
                    tool_name,
                    {id_name: value, "view": view, "limit": 5},
                )
            ) == f"{id_name} 必须是正整数"


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


def test_internal_errors_are_hidden_and_oversized_output_remains_valid_json() -> None:
    tools, api = build_tools()
    api.recent_result = "x" * (MAX_TOOL_OUTPUT_CHARS + 100)
    bounded = tools.execute(
        "get_recent_ranked_matches", {"nickname": "Alice", "limit": 1}
    )
    assert error_message(bounded) == "查询结果过大，请缩小查询范围"

    api.structured_result = {"payload": "界" * MAX_TOOL_OUTPUT_CHARS}
    structured = tools.execute(
        "get_global_dota_data", {"view": "hero_stats", "limit": 20}
    )
    assert error_message(structured) == "查询结果过大，请缩小查询范围"

    def fail(_: int, __: int) -> str:
        raise RuntimeError("secret internal detail")

    api.get_matches_by_date = fail
    result = tools.execute("get_today_matches", {"nickname": "Alice"})
    assert error_message(result) == "查询失败"
    assert "secret" not in result


def test_network_tools_fail_fast_when_global_query_slots_are_busy() -> None:
    tools, api = build_tools()

    class BusySlots:
        def acquire(self, *, blocking: bool) -> bool:
            assert blocking is False
            return False

        def release(self) -> None:
            raise AssertionError("未获取槽位时不能释放")

    tools._query_slots = BusySlots()  # type: ignore[assignment]

    result = tools.execute(
        "get_player_data",
        {"nickname": "Alice", "view": "profile", "days": 30, "limit": 5},
    )

    assert error_message(result) == "查询繁忙，请稍后重试"
    assert api.calls == []
    assert json.loads(tools.execute("list_tracked_players", {}))["players"]
