"""每日简报：汇总所有追踪选手近 24 小时数据，交给 DeepSeek 逐人点评。"""

import threading
import time
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
        cache_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.players = players
        self.api_client = api_client
        self.analyzer = analyzer
        self.cache_seconds = cache_seconds
        self.clock = clock
        self._cache_lock = threading.Lock()
        self._cached_report: str | None = None
        self._cached_at = 0.0

    def build(self) -> str:
        # 锁覆盖生成过程，使并发简报共用一次上游请求和 AI 分析。
        with self._cache_lock:
            now = self.clock()
            if (
                self._cached_report is not None
                and now - self._cached_at < self.cache_seconds
            ):
                return self._cached_report
            report = self._build_uncached()
            self._cached_report = report
            self._cached_at = self.clock()
            return report

    def _build_uncached(self) -> str:
        result = "根据距今24小时的数据分析\n"
        # 收集后一次拼接，避免逐个选手追加时反复复制已有报告内容。
        player_reports: list[str] = []
        unavailable_players: list[str] = []
        for nickname in self.players.nicknames():
            dota_id = self.players.get(nickname)
            if dota_id is None:
                continue
            recent_matches = self.api_client.get_matches_by_date(dota_id, 1)
            if recent_matches is None:
                unavailable_players.append(nickname)
                continue
            if recent_matches:
                player_reports.append(
                    f"{nickname}，id为{dota_id} 的近期数据是\n{recent_matches}"
                )
        if not player_reports:
            if unavailable_players:
                return result + "OpenDota 暂不可用，未能获取选手比赛数据。"
            return result + "暂无可分析的比赛数据。"

        report = result + self.analyzer("".join(player_reports))
        if unavailable_players:
            report += "\n未能获取：" + "、".join(unavailable_players)
        return report
