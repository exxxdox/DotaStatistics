"""每日简报：汇总所有追踪选手近 24 小时数据，交给 DeepSeek 逐人点评。"""

from collections.abc import Callable

from lib.deepseek_api import deepseek_dota_analyze
from lib.open_dota_client import OpenDotaApiClient
from lib.player_repository import PlayerRepository


class TodayReportService:
    """通过注入的选手仓库、OpenDota 客户端和 AI 分析器生成每日简报。"""

    def __init__(
        self,
        players: PlayerRepository,
        api_client: OpenDotaApiClient,
        analyzer: Callable[[str], str] = deepseek_dota_analyze,
    ) -> None:
        self.players = players
        self.api_client = api_client
        self.analyzer = analyzer

    def build(self) -> str:
        result = f"根据距今{24}小时的数据分析\n"
        ai_request_str = ""
        for nickname in self.players.nicknames():
            dota_id = self.players.get(nickname)
            if dota_id is None:
                continue
            recent_matches = self.api_client.get_matches_by_date(dota_id, 1)
            if recent_matches:
                ai_request_str += (
                    f"{nickname}，id为{dota_id} 的近期数据是\n{recent_matches}"
                )
        return result + self.analyzer(ai_request_str)
