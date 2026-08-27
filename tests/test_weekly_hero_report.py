from lib.open_dota_client import HeroWinRateStat
from service.weekly_hero_report import HeroWinRateReportService


class StubOpenDotaClient:
    def __init__(self, stats: list[HeroWinRateStat]) -> None:
        self.stats = stats
        self.arguments: tuple[int, int] | None = None

    def get_all_rank_win_rate_leaders(
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

    assert "当前全分段英雄胜率 Top 10" in report
    assert "1.敌法师 60.0%（200场）" in report
    assert "2.Axe 50.0%（150场）" in report
    assert "号位" not in report
    assert client.arguments is not None
    assert client.arguments == (10, 100)
