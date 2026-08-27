import logging
from collections.abc import Callable

from lib.deepseekapi import deepseekHeroRecommendations
from lib.open_dota_client import HeroWinRateStat, OpenDotaApiClient

_log = logging.getLogger(__name__)


class HeroWinRateReportService:
    """汇总 OpenDota 最近 30 个完整自然日并生成英雄胜率与位置推荐。"""

    def __init__(
        self,
        api_client: OpenDotaApiClient | None = None,
        hero_name_resolver: Callable[[int], str | None] | None = None,
        recommendation_analyzer: Callable[[str], str] | None = None,
        min_games: int = 100,
    ) -> None:
        self.api_client = api_client or OpenDotaApiClient()
        self.hero_name_resolver = hero_name_resolver
        self.recommendation_analyzer = (
            recommendation_analyzer or deepseekHeroRecommendations
        )
        self.min_games = min_games

    def build(self) -> str:
        stats = self.api_client.get_recent_month_win_rate_leaders(
            # AI 需要更宽的候选集，否则胜率前十可能缺少某些位置的常用英雄。
            top_count=40,
            min_games=self.min_games,
        )

        period_start = getattr(self.api_client, "stats_period_start", None)
        period_end = getattr(self.api_client, "stats_period_end", None)
        period = (
            f"{period_start:%Y-%m-%d} 至 {period_end:%Y-%m-%d}"
            if period_start and period_end
            else "最近30个完整自然日"
        )
        lines = [
            "最近30天全分段英雄胜率 Top 10",
            f"统计：{period}（UTC完整自然日）；至少 {self.min_games} 场。",
            "口径：OpenDota public_matches 公开比赛样本。",
        ]
        if getattr(self.api_client, "used_cached_hero_stats", False):
            lines.append("提示：OpenDota 暂不可用，当前展示最近一次成功缓存。")
        lines.extend(
            f"{index}.{self._hero_name(item)} {item.win_rate:.1%}（{item.games}场）"
            for index, item in enumerate(stats[:10], start=1)
        )
        if not stats:
            lines.append("当前统计周期样本不足。")
        else:
            lines.extend(["", "DeepSeek 1—5号位推荐（基于常见定位分析）："])
            try:
                recommendation = self.recommendation_analyzer(
                    self._build_analysis_input(stats, period)
                ).strip()
                lines.append(recommendation or "DeepSeek 未返回推荐结果。")
            except Exception:
                # AI 是附加分析，失败时仍应交付已经生成的客观胜率榜。
                _log.exception("DeepSeek 英雄位置推荐失败")
                lines.append("DeepSeek 推荐暂不可用，请稍后再试。")
        return "\n".join(lines)

    def _build_analysis_input(
        self, stats: list[HeroWinRateStat], period: str
    ) -> str:
        candidates = "\n".join(
            f"{self._hero_name(item)}：胜率 {item.win_rate:.2%}，{item.games} 场"
            for item in stats
        )
        return (
            f"统计周期：{period}\n"
            "数据来源：OpenDota public_matches 公开比赛样本，全部段位。\n"
            "请从以下候选中完成1至5号位推荐：\n"
            f"{candidates}"
        )

    def _hero_name(self, stat: HeroWinRateStat) -> str:
        if self.hero_name_resolver:
            resolved_name = self.hero_name_resolver(stat.hero_id)
            if resolved_name:
                return resolved_name
        return stat.hero_name


# 保留旧名称，避免其他模块或部署中的扩展代码立即失效。
WeeklyHeroReportService = HeroWinRateReportService
