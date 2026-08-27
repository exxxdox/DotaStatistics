from datetime import datetime
from zoneinfo import ZoneInfo

from lib.open_dota_client import HeroPositionStat
from service.weekly_hero_report import WeeklyHeroReportService, get_previous_week_range


class StubOpenDotaClient:
    def __init__(self, stats: list[HeroPositionStat]) -> None:
        self.stats = stats
        self.arguments: tuple[int, int, int, int] | None = None

    def get_position_win_rate_leaders(
        self,
        start_timestamp: int,
        end_timestamp: int,
        top_count: int,
        min_games: int,
    ) -> list[HeroPositionStat]:
        self.arguments = (start_timestamp, end_timestamp, top_count, min_games)
        return self.stats


def test_previous_week_uses_complete_natural_week() -> None:
    now = datetime(2026, 8, 27, 20, tzinfo=ZoneInfo("Asia/Shanghai"))

    start, end = get_previous_week_range(now)

    assert start == datetime(2026, 8, 17, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert end == datetime(2026, 8, 24, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_report_formats_three_leaders_and_missing_positions() -> None:
    stats = [
        HeroPositionStat(1, 1, "Anti-Mage", 20, 12),
        HeroPositionStat(1, 2, "Axe", 10, 5),
    ]
    client = StubOpenDotaClient(stats)
    service = WeeklyHeroReportService(
        api_client=client,
        hero_name_resolver=lambda hero_id: "敌法师" if hero_id == 1 else None,
        min_games=10,
    )

    report = service.build(
        datetime(2026, 8, 27, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    assert "08-17 至 08-23" in report
    assert "1.敌法师 60.0%（20场）" in report
    assert "2.Axe 50.0%（10场）" in report
    assert "5号位：样本不足" in report
    assert client.arguments is not None
    assert client.arguments[2:] == (3, 10)
