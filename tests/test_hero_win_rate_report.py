from datetime import date

from lib.open_dota_client import HeroWinRateStat
from service.hero_win_rate_report import HeroWinRateReportService


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
    service = HeroWinRateReportService(
        api_client=client,
        hero_name_resolver=lambda hero_id: "敌法师" if hero_id == 1 else None,
        min_games=100,
    )

    report = service.build()

    assert "最近30天全分段英雄胜率 Top 10" in report
    assert "2026-07-28 至 2026-08-26（UTC完整自然日）" in report
    assert "OpenDota public_matches 公开比赛样本" in report
    assert "1.敌法师 60.0%（200场）" in report
    assert "2.Axe 50.0%（150场）" in report
    assert "DeepSeek" not in report
    assert "推荐" not in report
    assert client.arguments is not None
    assert client.arguments == (10, 100)


def test_report_marks_cached_open_dota_data() -> None:
    client = StubOpenDotaClient([])
    client.used_cached_hero_stats = True

    report = HeroWinRateReportService(api_client=client).build()

    assert "当前展示最近一次成功缓存" in report


def test_report_is_capped_at_ten_rows() -> None:
    client = StubOpenDotaClient([
        HeroWinRateStat(index, f"Hero {index}", 200, 120)
        for index in range(1, 12)
    ])

    report = HeroWinRateReportService(api_client=client).build()

    assert "10.Hero 10" in report
    assert "11.Hero 11" not in report


def test_empty_statistics_report_has_no_ai_analysis() -> None:
    report = HeroWinRateReportService(api_client=StubOpenDotaClient([])).build()

    assert "当前统计周期样本不足。" in report
    assert "DeepSeek" not in report
