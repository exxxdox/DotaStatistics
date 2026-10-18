"""AI 可调用的只读 Dota 工具；只暴露已绑定选手和固定查询。"""

import json
import threading
from typing import Any

from lib.open_dota_client import OpenDotaApiClient
from lib.player_repository import PlayerRepository


MAX_TOOL_OUTPUT_CHARS = 8_000
PLAYER_VIEWS = [
    "profile",
    "win_loss",
    "heroes",
    "peers",
    "totals",
    "counts",
    "ratings",
    "rankings",
]
HERO_VIEWS = ["matchups", "durations", "recent_matches", "rankings"]
TEAM_VIEWS = ["overview", "matches", "players", "heroes"]
GLOBAL_VIEWS = [
    "live_matches",
    "pro_matches",
    "public_matches",
    "top_players",
    "hero_stats",
    "pro_players",
]
NETWORK_TOOLS = {
    "get_recent_ranked_matches",
    "get_today_matches",
    "get_player_data",
    "get_match_data",
    "get_hero_data",
    "get_team_data",
    "get_global_dota_data",
}


class DotaAgentTools:
    """声明并执行 AI 工具，所有外部输入都在此处严格校验。"""

    def __init__(
        self,
        players: PlayerRepository,
        api_client: OpenDotaApiClient,
    ) -> None:
        self.players = players
        self.api_client = api_client
        # 跨会话共享上限，避免并发聊天耗尽 OpenDota 配额和工作线程。
        self._query_slots = threading.BoundedSemaphore(4)

    @property
    def definitions(self) -> list[dict[str, Any]]:
        nickname_parameter = {
            "type": "string",
            "description": "已绑定的选手昵称，必须与列表中的昵称完全一致。",
        }
        limit_parameter = {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "最多返回的记录数。",
        }
        return [
            self._definition(
                "list_tracked_players",
                "列出机器人已绑定、可供查询的选手昵称。",
                {},
                [],
            ),
            self._definition(
                "get_recent_ranked_matches",
                "查询已绑定选手最近 1 至 5 场天梯比赛。",
                {
                    "nickname": nickname_parameter,
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                        "description": "返回场数。",
                    },
                },
                ["nickname", "limit"],
            ),
            self._definition(
                "get_today_matches",
                "查询已绑定选手近 24 小时的天梯比赛。",
                {"nickname": nickname_parameter},
                ["nickname"],
            ),
            self._definition(
                "get_player_data",
                "按视图查询已绑定选手的 OpenDota 资料或统计。",
                {
                    "nickname": nickname_parameter,
                    "view": {"type": "string", "enum": PLAYER_VIEWS},
                    "days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 365,
                        "description": "统计最近多少天。",
                    },
                    "limit": limit_parameter,
                },
                ["nickname", "view", "days", "limit"],
            ),
            self._definition(
                "get_match_data",
                "按比赛 ID 查询一场比赛的 OpenDota 数据。",
                {
                    "match_id": {
                        "type": "integer",
                        "minimum": 1,
                    }
                },
                ["match_id"],
            ),
            self._definition(
                "get_hero_data",
                "按视图查询英雄的 OpenDota 数据。",
                {
                    "hero_id": {"type": "integer", "minimum": 1},
                    "view": {"type": "string", "enum": HERO_VIEWS},
                    "limit": limit_parameter,
                },
                ["hero_id", "view", "limit"],
            ),
            self._definition(
                "get_team_data",
                "按视图查询职业战队的 OpenDota 数据。",
                {
                    "team_id": {"type": "integer", "minimum": 1},
                    "view": {"type": "string", "enum": TEAM_VIEWS},
                    "limit": limit_parameter,
                },
                ["team_id", "view", "limit"],
            ),
            self._definition(
                "get_global_dota_data",
                "按视图查询实时比赛、职业比赛或全局统计。",
                {
                    "view": {"type": "string", "enum": GLOBAL_VIEWS},
                    "limit": limit_parameter,
                },
                ["view", "limit"],
            ),
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        handlers = {
            "list_tracked_players": self._list_tracked_players,
            "get_recent_ranked_matches": self._get_recent_ranked_matches,
            "get_today_matches": self._get_today_matches,
            "get_player_data": self._get_player_data,
            "get_match_data": self._get_match_data,
            "get_hero_data": self._get_hero_data,
            "get_team_data": self._get_team_data,
            "get_global_dota_data": self._get_global_dota_data,
        }
        # 模型输出仍是不可信输入；先验类型检查避免不可哈希名称逃出工具边界。
        if not isinstance(name, str):
            return self._error("未知工具")
        handler = handlers.get(name)
        if handler is None:
            return self._error("未知工具")
        if not isinstance(arguments, dict):
            return self._error("参数必须是对象")

        needs_slot = name in NETWORK_TOOLS
        if needs_slot and not self._query_slots.acquire(blocking=False):
            return self._error("查询繁忙，请稍后重试")
        try:
            try:
                return self._limit_output(handler(arguments))
            except Exception:
                # 工具错误不得终止整轮对话，也不能向模型泄露内部异常细节。
                return self._error("查询失败")
        finally:
            if needs_slot:
                self._query_slots.release()

    @staticmethod
    def _definition(
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str],
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }

    def _list_tracked_players(self, arguments: dict[str, Any]) -> str:
        error = self._validate_fields(arguments, set())
        if error:
            return error
        return json.dumps(
            {"players": self.players.nicknames()}, ensure_ascii=False, separators=(",", ":")
        )

    def _get_recent_ranked_matches(self, arguments: dict[str, Any]) -> str:
        error = self._validate_fields(arguments, {"nickname", "limit"})
        if error:
            return error
        nickname = arguments["nickname"]
        limit = arguments["limit"]
        if not isinstance(nickname, str) or not nickname.strip():
            return self._error("nickname 必须是非空字符串")
        if type(limit) is not int or not 1 <= limit <= 5:
            return self._error("limit 必须是 1 至 5 的整数")
        dota_id = self.players.get(nickname)
        if dota_id is None:
            return self._error("选手未绑定")
        result = self.api_client.get_recent_matches(dota_id, limit)
        if result is None:
            return self._error("OpenDota 暂不可用")
        return result if result.strip() else "暂时没有查到近期天梯比赛。"

    def _get_today_matches(self, arguments: dict[str, Any]) -> str:
        error = self._validate_fields(arguments, {"nickname"})
        if error:
            return error
        nickname = arguments["nickname"]
        if not isinstance(nickname, str) or not nickname.strip():
            return self._error("nickname 必须是非空字符串")
        dota_id = self.players.get(nickname)
        if dota_id is None:
            return self._error("选手未绑定")
        result = self.api_client.get_matches_by_date(dota_id, 1)
        if result is None:
            return self._error("OpenDota 暂不可用")
        return result if result.strip() else "该选手近 24 小时没有天梯比赛。"

    def _get_player_data(self, arguments: dict[str, Any]) -> str:
        required = {"nickname", "view", "days", "limit"}
        error = self._validate_fields(arguments, required)
        if error:
            return error
        nickname = arguments["nickname"]
        view = arguments["view"]
        days = arguments["days"]
        limit = arguments["limit"]
        if not isinstance(nickname, str) or not nickname.strip():
            return self._error("nickname 必须是非空字符串")
        dota_id = self.players.get(nickname)
        if dota_id is None:
            return self._error("选手未绑定")
        error = self._validate_view(view, PLAYER_VIEWS)
        if error:
            return error
        error = self._validate_integer(days, "days", 1, 365)
        if error:
            return error
        error = self._validate_integer(limit, "limit", 1, 20)
        if error:
            return error
        result = self.api_client.get_player_data(
            dota_id,
            view,
            days=days,
            limit=limit,
        )
        return self._serialize(result)

    def _get_match_data(self, arguments: dict[str, Any]) -> str:
        error = self._validate_fields(arguments, {"match_id"})
        if error:
            return error
        match_id = arguments["match_id"]
        error = self._validate_positive_id(match_id, "match_id")
        if error:
            return error
        return self._serialize(self.api_client.get_match_data(match_id))

    def _get_hero_data(self, arguments: dict[str, Any]) -> str:
        required = {"hero_id", "view", "limit"}
        error = self._validate_fields(arguments, required)
        if error:
            return error
        hero_id = arguments["hero_id"]
        view = arguments["view"]
        limit = arguments["limit"]
        error = self._validate_positive_id(hero_id, "hero_id")
        if error:
            return error
        error = self._validate_view(view, HERO_VIEWS)
        if error:
            return error
        error = self._validate_integer(limit, "limit", 1, 20)
        if error:
            return error
        return self._serialize(
            self.api_client.get_hero_data(hero_id, view, limit=limit)
        )

    def _get_team_data(self, arguments: dict[str, Any]) -> str:
        required = {"team_id", "view", "limit"}
        error = self._validate_fields(arguments, required)
        if error:
            return error
        team_id = arguments["team_id"]
        view = arguments["view"]
        limit = arguments["limit"]
        error = self._validate_positive_id(team_id, "team_id")
        if error:
            return error
        error = self._validate_view(view, TEAM_VIEWS)
        if error:
            return error
        error = self._validate_integer(limit, "limit", 1, 20)
        if error:
            return error
        return self._serialize(
            self.api_client.get_team_data(team_id, view, limit=limit)
        )

    def _get_global_dota_data(self, arguments: dict[str, Any]) -> str:
        error = self._validate_fields(arguments, {"view", "limit"})
        if error:
            return error
        view = arguments["view"]
        limit = arguments["limit"]
        error = self._validate_view(view, GLOBAL_VIEWS)
        if error:
            return error
        error = self._validate_integer(limit, "limit", 1, 20)
        if error:
            return error
        return self._serialize(self.api_client.get_global_data(view, limit=limit))

    @classmethod
    def _validate_fields(
        cls,
        arguments: dict[str, Any],
        required: set[str],
    ) -> str | None:
        actual = set(arguments)
        if actual == required:
            return None
        if actual - required:
            return cls._error("包含未允许的参数")
        return cls._error("缺少必填参数")

    @classmethod
    def _validate_view(cls, value: Any, allowed: list[str]) -> str | None:
        if not isinstance(value, str) or value not in allowed:
            return cls._error("未知 view")
        return None

    @classmethod
    def _validate_positive_id(cls, value: Any, name: str) -> str | None:
        return cls._validate_integer(value, name, 1)

    @classmethod
    def _validate_integer(
        cls,
        value: Any,
        name: str,
        minimum: int,
        maximum: int | None = None,
    ) -> str | None:
        if type(value) is not int or value < minimum or (
            maximum is not None and value > maximum
        ):
            if maximum is not None:
                return cls._error(f"{name} 必须是 {minimum} 至 {maximum} 的整数")
            return cls._error(f"{name} 必须是正整数")
        return None

    @staticmethod
    def _serialize(result: Any) -> str:
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _error(message: str) -> str:
        return json.dumps({"error": message}, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _limit_output(output: str) -> str:
        if len(output) <= MAX_TOOL_OUTPUT_CHARS:
            return output
        # 截断 JSON 会制造无法解析的数据；明确拒绝大结果，让模型缩小查询范围。
        return DotaAgentTools._error("查询结果过大，请缩小查询范围")
