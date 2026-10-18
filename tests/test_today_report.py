from unittest.mock import Mock

from service.today import TodayReportService


class FakePlayers:
    def __init__(self, records: dict[str, int]) -> None:
        self._records = records

    def nicknames(self) -> list[str]:
        return list(self._records)

    def get(self, nickname: str) -> int | None:
        return self._records.get(nickname)


class FakeApiClient:
    def __init__(self, data: dict[int, str | None]) -> None:
        self._data = data
        self.calls = 0

    def get_matches_by_date(self, account_id: int, date: int) -> str | None:
        self.calls += 1
        return self._data.get(account_id, "")


def test_build_feeds_each_player_data_to_analyzer() -> None:
    players = FakePlayers({"小明": 123, "小红": 456})
    api_client = FakeApiClient(
        {
            123: "近况数据A",
            456: "近况数据B",
        }
    )
    analyzed: list[str] = []

    def analyzer(prompt: str) -> str:
        analyzed.append(prompt)
        return "点评完毕"

    report = TodayReportService(
        players=players, api_client=api_client, analyzer=analyzer
    ).build()

    assert report == "根据距今24小时的数据分析\n点评完毕"
    assert len(analyzed) == 1
    assert "小明，id为123 的近期数据是\n近况数据A" in analyzed[0]
    assert "小红，id为456 的近期数据是\n近况数据B" in analyzed[0]


def test_build_skips_players_without_recent_matches() -> None:
    players = FakePlayers({"小明": 123, "小红": 456})
    api_client = FakeApiClient({123: "近况数据A"})
    prompts: list[str] = []

    TodayReportService(
        players=players,
        api_client=api_client,
        analyzer=lambda prompt: prompts.append(prompt) or "",
    ).build()

    assert len(prompts) == 1
    assert "小明" in prompts[0]
    assert "小红" not in prompts[0]


def test_build_without_data_skips_analyzer() -> None:
    analyzer = Mock()

    report = TodayReportService(
        players=FakePlayers({}), api_client=FakeApiClient({}), analyzer=analyzer
    ).build()

    assert report.endswith("暂无可分析的比赛数据。")
    analyzer.assert_not_called()


def test_build_reports_upstream_failure_without_calling_analyzer() -> None:
    analyzer = Mock()

    report = TodayReportService(
        players=FakePlayers({"小明": 123}),
        api_client=FakeApiClient({123: None}),
        analyzer=analyzer,
    ).build()

    assert "OpenDota 暂不可用" in report
    analyzer.assert_not_called()


def test_build_reuses_short_lived_report() -> None:
    now = [10.0]
    api_client = FakeApiClient({123: "比赛"})
    service = TodayReportService(
        players=FakePlayers({"小明": 123}),
        api_client=api_client,
        analyzer=lambda _prompt: "点评",
        cache_seconds=60,
        clock=lambda: now[0],
    )

    assert service.build() == service.build()
    assert api_client.calls == 1

    now[0] += 60
    service.build()
    assert api_client.calls == 2
