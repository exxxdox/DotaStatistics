from collections.abc import Callable
from lib.open_dota_client import HeroWinRateStat, OpenDotaApiClient


class HeroWinRateReportService:
    """查询 OpenDota 最近四个统计周并生成英雄胜率榜。"""

    def __init__(
        self,
        api_client: OpenDotaApiClient | None = None,
        hero_name_resolver: Callable[[int], str | None] | None = None,
        min_games: int = 100,
    ) -> None:
        self.api_client = api_client or OpenDotaApiClient()
        self.hero_name_resolver = hero_name_resolver
        self.min_games = min_games

    def build(self) -> str:
        stats = self.api_client.get_recent_month_win_rate_leaders(
            top_count=10,
            min_games=self.min_games,
        )

        period_start = getattr(self.api_client, "stats_period_start", None)
        period_end = getattr(self.api_client, "stats_period_end", None)
        period = (
            f"{period_start:%Y-%m-%d} 至 {period_end:%Y-%m-%d}"
            if period_start and period_end
            else "最近四个统计周"
        )
        lines = [
            "最近一个月全分段英雄胜率 Top 10",
            f"统计：{period}（当前周及前三周）；至少 {self.min_games} 场。",
        ]
        if getattr(self.api_client, "used_cached_hero_stats", False):
            lines.append("提示：OpenDota 暂不可用，当前展示最近一次成功缓存。")
        lines.extend(
            f"{index}.{self._hero_name(item)} {item.win_rate:.1%}（{item.games}场）"
            for index, item in enumerate(stats, start=1)
        )
        if not stats:
            lines.append("当前统计周期样本不足。")
        return "\n".join(lines)

    def _hero_name(self, stat: HeroWinRateStat) -> str:
        if self.hero_name_resolver:
            resolved_name = self.hero_name_resolver(stat.hero_id)
            if resolved_name:
                return resolved_name
        return stat.hero_name


# 保留旧名称，避免其他模块或部署中的扩展代码立即失效。
WeeklyHeroReportService = HeroWinRateReportService
