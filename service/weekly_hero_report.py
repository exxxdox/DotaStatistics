from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lib.open_dota_client import HeroPositionStat, OpenDotaApiClient

SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


def get_previous_week_range(
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    current = now.astimezone(SHANGHAI_TIMEZONE) if now else datetime.now(SHANGHAI_TIMEZONE)
    current_week_start = (current - timedelta(days=current.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return current_week_start - timedelta(days=7), current_week_start


class WeeklyHeroReportService:
    """查询上一自然周数据并生成适合 QQ 群发送的简报。"""

    def __init__(
        self,
        api_client: OpenDotaApiClient | None = None,
        hero_name_resolver: Callable[[int], str | None] | None = None,
        min_games: int = 3,
    ) -> None:
        self.api_client = api_client or OpenDotaApiClient()
        self.hero_name_resolver = hero_name_resolver
        self.min_games = min_games

    def build(self, now: datetime | None = None) -> str:
        start, end = get_previous_week_range(now)
        stats = self.api_client.get_position_win_rate_leaders(
            start_timestamp=int(start.timestamp()),
            end_timestamp=int(end.timestamp()),
            top_count=3,
            min_games=self.min_games,
        )

        grouped = self._group_by_position(stats)
        lines = [
            f"上周五位置英雄胜率榜（{start:%m-%d} 至 {(end - timedelta(days=1)):%m-%d}）",
            f"口径：职业队长模式；位置按同队 GPM 排名；至少 {self.min_games} 场。",
        ]
        for position in range(1, 6):
            leaders = grouped.get(position, [])
            if not leaders:
                lines.append(f"{position}号位：样本不足")
                continue
            summary = "；".join(
                f"{index}.{self._hero_name(item)} {item.win_rate:.1%}（{item.games}场）"
                for index, item in enumerate(leaders, start=1)
            )
            lines.append(f"{position}号位：{summary}")
        return "\n".join(lines)

    def _hero_name(self, stat: HeroPositionStat) -> str:
        if self.hero_name_resolver:
            resolved_name = self.hero_name_resolver(stat.hero_id)
            if resolved_name:
                return resolved_name
        return stat.hero_name

    @staticmethod
    def _group_by_position(
        stats: list[HeroPositionStat],
    ) -> dict[int, list[HeroPositionStat]]:
        grouped: dict[int, list[HeroPositionStat]] = {}
        for stat in stats:
            grouped.setdefault(stat.position, []).append(stat)
        return grouped
