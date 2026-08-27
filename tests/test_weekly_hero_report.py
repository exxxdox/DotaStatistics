from datetime import date

from lib.open_dota_client import HeroWinRateStat
from service.weekly_hero_report import HeroWinRateReportService


class StubOpenDotaClient:
    def __init__(self, stats: list[HeroWinRateStat]) -> None:
        self.stats = stats
        self.arguments: tuple[int, int] | None = None
        self.stats_period_start = date(2026, 7, 28)
        self.stats_period_end = date(2026, 8, 26)

    def get_recent_month_win_rate_leaders(
        self,
        top_count: int,
        min_games: int,
    ) -> list[HeroWinRateStat]:
        self.arguments = (top_count, min_games)
        return self.stats


def test_report_formats_all_rank_top_ten_without_positions() -> None:
    stats = [
        HeroWinRateStat(1, "Anti-Mage", 200, 120),
        HeroWinRateStat(2, "Axe", 150, 75),
    ]
    client = StubOpenDotaClient(stats)
    analysis_inputs: list[str] = []

    def analyze(prompt: str) -> str:
        analysis_inputs.append(prompt)
        return "1号位：敌法师（60.00%，200场）- 后期核心。"

    service = HeroWinRateReportService(
        api_client=client,
        hero_name_resolver=lambda hero_id: "敌法师" if hero_id == 1 else None,
        recommendation_analyzer=analyze,
        min_games=100,
    )

    report = service.build()

    assert "最近30天全分段英雄胜率 Top 10" in report
    assert "2026-07-28 至 2026-08-26（UTC完整自然日）" in report
    assert "OpenDota public_matches 公开比赛样本" in report
    assert "1.敌法师 60.0%（200场）" in report
    assert "2.Axe 50.0%（150场）" in report
    assert "DeepSeek 1—5号位推荐（基于常见定位分析）" in report
    assert "1号位：敌法师" in report
    assert client.arguments is not None
    assert client.arguments == (40, 100)
    assert len(analysis_inputs) == 1
    assert "敌法师：胜率 60.00%，200 场" in analysis_inputs[0]
    assert "Axe：胜率 50.00%，150 场" in analysis_inputs[0]


def test_report_marks_cached_open_dota_data() -> None:
    client = StubOpenDotaClient([])
    client.used_cached_hero_stats = True

    report = HeroWinRateReportService(api_client=client).build()

    assert "当前展示最近一次成功缓存" in report


def test_report_keeps_objective_stats_when_deepseek_fails() -> None:
    client = StubOpenDotaClient([HeroWinRateStat(1, "Anti-Mage", 200, 120)])

    def fail_analysis(_: str) -> str:
        raise RuntimeError("DeepSeek unavailable")

    report = HeroWinRateReportService(
        api_client=client,
        recommendation_analyzer=fail_analysis,
    ).build()

    assert "1.Anti-Mage 60.0%（200场）" in report
    assert "DeepSeek 推荐暂不可用，请稍后再试。" in report
