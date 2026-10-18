"""AI 可调用的只读 Dota 工具；只暴露已绑定选手和固定查询。"""

import json
from typing import Any

from lib.open_dota_client import OpenDotaApiClient
from lib.player_repository import PlayerRepository


MAX_TOOL_OUTPUT_CHARS = 8_000


class DotaAgentTools:
    """声明并执行 AI 工具，所有外部输入都在此处严格校验。"""

    def __init__(
        self,
        players: PlayerRepository,
        api_client: OpenDotaApiClient,
    ) -> None:
        self.players = players
        self.api_client = api_client

    @property
    def definitions(self) -> list[dict[str, Any]]:
        nickname_parameter = {
            "type": "string",
            "description": "已绑定的选手昵称，必须与列表中的昵称完全一致。",
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
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        handlers = {
            "list_tracked_players": self._list_tracked_players,
            "get_recent_ranked_matches": self._get_recent_ranked_matches,
            "get_today_matches": self._get_today_matches,
        }
        # 模型输出仍是不可信输入；先验类型检查避免不可哈希名称逃出工具边界。
        if not isinstance(name, str):
            return self._error("未知工具")
        handler = handlers.get(name)
        if handler is None:
            return self._error("未知工具")
        if not isinstance(arguments, dict):
            return self._error("参数必须是对象")

        try:
            return self._limit_output(handler(arguments))
        except Exception:
            # 工具错误不得终止整轮对话，也不能向模型泄露内部异常细节。
            return self._error("查询失败")

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

    @staticmethod
    def _error(message: str) -> str:
        return json.dumps({"error": message}, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _limit_output(output: str) -> str:
        if len(output) <= MAX_TOOL_OUTPUT_CHARS:
            return output
        suffix = "\n[输出已截断]"
        return output[: MAX_TOOL_OUTPUT_CHARS - len(suffix)] + suffix
