from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import requests

from lib.open_dota_client import OpenDotaApiClient, OpenDotaApiError


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Server Error",
                response=self,
            )

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.headers: dict[str, str] = {}
        self.calls = 0

    def get(self, *_args, **_kwargs) -> FakeResponse:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def test_daily_query_uses_exact_utc_day_boundaries() -> None:
    client = OpenDotaApiClient(cache_path=None)
    captured_sql = ""

    def explorer(sql: str) -> list[dict[str, Any]]:
        nonlocal captured_sql
        captured_sql = sql
        return [{"hero_id": 1, "games": "10", "wins": "6"}]

    client.explorer = explorer  # type: ignore[method-assign]
    rows = client._fetch_daily_stats(date(2026, 8, 1))

    start = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2026, 8, 2, tzinfo=timezone.utc).timestamp())
    assert rows[0]["games"] == "10"
    assert f"start_time >= {start}" in captured_sql
    assert f"start_time < {end}" in captured_sql
    assert "WITH filtered_matches AS" in captured_sql
    assert "unnest(radiant_team)" in captured_sql
    assert "unnest(dire_team)" in captured_sql
    assert "CROSS JOIN LATERAL" not in captured_sql


@pytest.mark.parametrize(
    "timeout_detail",
    [
        "canceling statement due to statement timeout",
        "Error: Query read timeout",
    ],
)
def test_daily_query_splits_and_merges_on_query_timeout(
    timeout_detail: str,
) -> None:
    client = OpenDotaApiClient(cache_path=None)
    captured_sql: list[str] = []

    def explorer(sql: str) -> list[dict[str, Any]]:
        captured_sql.append(sql)
        if len(captured_sql) == 1:
            raise OpenDotaApiError(
                f"OpenDota 请求失败: HTTP 400, detail={timeout_detail}"
            )
        return [{"hero_id": 1, "games": 10, "wins": 6}]

    client.explorer = explorer  # type: ignore[method-assign]
    rows = client._fetch_daily_stats(date(2026, 8, 1))

    start = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
    midpoint = int(datetime(2026, 8, 1, 12, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2026, 8, 2, tzinfo=timezone.utc).timestamp())
    assert len(captured_sql) == 3
    assert f"start_time >= {start}" in captured_sql[1]
    assert f"start_time < {midpoint}" in captured_sql[1]
    assert f"start_time >= {midpoint}" in captured_sql[2]
    assert f"start_time < {end}" in captured_sql[2]
    assert rows == [{"hero_id": 1, "games": 20, "wins": 12}]


def test_monthly_stats_backfill_once_then_reuse_daily_cache() -> None:
    fixed_now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    cache_path = Path(__file__).parent / ".daily_hero_stats_cache_test.json"
    calls: list[date] = []

    def fetch(day: date) -> list[dict[str, Any]]:
        calls.append(day)
        return [
            {"hero_id": 1, "games": 10, "wins": 6},
            {"hero_id": 2, "games": 20, "wins": 10},
        ]

    try:
        client = OpenDotaApiClient(
            cache_path=cache_path,
            refresh_workers=1,
            now=lambda: fixed_now,
        )
        client._fetch_daily_stats = fetch  # type: ignore[method-assign]
        stats = client.get_recent_month_win_rate_leaders(top_count=10, min_games=1)

        assert len(calls) == 30
        assert calls[0] == date(2026, 7, 28)
        assert calls[-1] == date(2026, 8, 26)
        hero_one = next(stat for stat in stats if stat.hero_id == 1)
        assert (hero_one.games, hero_one.wins, hero_one.win_rate) == (300, 180, 0.6)
        assert client.stats_period_start == date(2026, 7, 28)
        assert client.stats_period_end == date(2026, 8, 26)

        cached_client = OpenDotaApiClient(
            cache_path=cache_path,
            refresh_workers=1,
            now=lambda: fixed_now,
        )
        cached_client._fetch_daily_stats = (  # type: ignore[method-assign]
            lambda _day: pytest.fail("已有完整日缓存时不应请求 OpenDota")
        )
        cached_stats = cached_client.get_recent_month_win_rate_leaders(
            top_count=10, min_games=1
        )

        assert next(stat for stat in cached_stats if stat.hero_id == 1).games == 300
    finally:
        cache_path.unlink(missing_ok=True)
        cache_path.with_suffix(".json.tmp").unlink(missing_ok=True)


def test_refresh_failure_uses_recent_complete_snapshot() -> None:
    first_now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    cache_path = Path(__file__).parent / ".daily_snapshot_cache_test.json"
    try:
        client = OpenDotaApiClient(
            cache_path=cache_path,
            refresh_workers=1,
            now=lambda: first_now,
        )
        client._fetch_daily_stats = (  # type: ignore[method-assign]
            lambda _day: [{"hero_id": 1, "games": 10, "wins": 6}]
        )
        client.get_recent_month_win_rate_leaders(top_count=10, min_games=1)

        cached_client = OpenDotaApiClient(
            cache_path=cache_path,
            refresh_workers=1,
            now=lambda: first_now + timedelta(days=1),
        )

        def fail(_day: date) -> list[dict[str, Any]]:
            raise OpenDotaApiError("OpenDota 暂不可用")

        cached_client._fetch_daily_stats = fail  # type: ignore[method-assign]
        stats = cached_client.get_recent_month_win_rate_leaders(
            top_count=10, min_games=1
        )

        assert stats[0].games == 300
        assert cached_client.used_cached_hero_stats is True
        assert cached_client.stats_period_end == date(2026, 8, 26)
    finally:
        cache_path.unlink(missing_ok=True)
        cache_path.with_suffix(".json.tmp").unlink(missing_ok=True)


def test_monthly_stats_reject_invalid_limits_and_rows() -> None:
    client = OpenDotaApiClient(cache_path=None)
    with pytest.raises(ValueError):
        client.get_recent_month_win_rate_leaders(top_count=0)
    with pytest.raises(OpenDotaApiError):
        client._aggregate_monthly_stats([{"hero_id": 1, "games": 5, "wins": 6}])


def test_get_retries_522_before_succeeding() -> None:
    session = FakeSession(
        [FakeResponse(522), FakeResponse(522), FakeResponse(200, {"ok": True})]
    )
    sleeps: list[float] = []
    client = OpenDotaApiClient(
        session=session,
        max_retries=2,
        retry_backoff=1,
        cache_path=None,
        sleep=sleeps.append,
    )

    assert client._get("/heroStats") == {"ok": True}
    assert session.calls == 3
    assert sleeps == [1, 2]


def test_get_reports_sanitized_api_error_without_full_url() -> None:
    client = OpenDotaApiClient(
        session=FakeSession([FakeResponse(400, {"error": "bad\nquery"})]),
        max_retries=0,
        cache_path=None,
    )

    with pytest.raises(OpenDotaApiError) as captured:
        client._get("/explorer", params={"sql": "SELECT secret"})

    assert str(captured.value) == (
        "OpenDota 请求失败: HTTP 400, path=/explorer, detail=bad query"
    )
    assert "SELECT secret" not in str(captured.value)


def test_get_player_wl_returns_win_loss_tuple() -> None:
    client = OpenDotaApiClient(
        session=FakeSession([FakeResponse(200, {"win": 3, "lose": 1})]),
        max_retries=0,
        cache_path=None,
    )

    assert client.get_player_wl(123, 1) == (3, 1)
    assert client.session.calls == 1


def test_get_player_wl_returns_none_on_api_error() -> None:
    client = OpenDotaApiClient(
        session=FakeSession([FakeResponse(502, {"error": "boom"})]),
        max_retries=0,
        cache_path=None,
    )

    assert client.get_player_wl(123, 1) is None


def test_get_recent_matches_filters_non_ranked_and_limits() -> None:
    recent = [
        {
            "game_mode": 22,
            "radiant_win": True,
            "player_slot": 0,
            "hero_id": 1,
            "kills": 5,
            "deaths": 3,
            "assists": 2,
            "hero_damage": 100,
            "hero_healing": 0,
            "gold_per_min": 500,
        },
        {"game_mode": 23, "hero_id": 2},  # 非天梯，应被过滤
        {
            "game_mode": 22,
            "radiant_win": False,
            "player_slot": 128,
            "hero_id": 3,
            "kills": 1,
            "deaths": 9,
            "assists": 0,
            "hero_damage": 20,
            "hero_healing": 0,
            "gold_per_min": 200,
        },
    ]
    client = OpenDotaApiClient(
        session=FakeSession([FakeResponse(200, recent)]),
        max_retries=0,
        cache_path=None,
        hero_name_resolver=lambda hero_id: "敌法师" if hero_id == 1 else None,
    )

    result = client.get_recent_matches(123, limit=2)

    assert result is not None
    assert result.count("敌法师") == 1
    assert result.count("英雄 3") == 1
    assert client.session.calls == 1


def test_get_recent_matches_returns_none_on_api_error() -> None:
    client = OpenDotaApiClient(
        session=FakeSession([FakeResponse(522)]),
        max_retries=0,
        cache_path=None,
    )

    assert client.get_recent_matches(123) is None


def test_get_matches_by_date_formats_detail_and_appends_end_marker() -> None:
    matches = [
        {
            "game_mode": 22,
            "match_id": 9,
            "radiant_win": True,
            "player_slot": 0,
            "hero_id": 1,
            "kills": 5,
            "deaths": 3,
            "assists": 2,
        }
    ]
    detail = {
        "duration": 3000,
        "players": [
            {
                "account_id": 123,
                "isRadiant": True,
                "gold_per_min": 500,
                "xp_per_min": 400,
                "hero_damage": 900,
                "tower_damage": 100,
                "hero_healing": 0,
                "total_gold": 10000,
                "total_xp": 9000,
            }
        ],
    }
    client = OpenDotaApiClient(
        session=FakeSession([FakeResponse(200, matches), FakeResponse(200, detail)]),
        max_retries=0,
        cache_path=None,
        hero_name_resolver=lambda hero_id: "敌法师" if hero_id == 1 else None,
    )

    result = client.get_matches_by_date(123, 1)

    assert "游戏胜利 英雄:敌法师 击杀:5 死亡:3 助攻:2" in result
    assert "持续时间3000秒" in result
    assert "此玩家数据结束" in result


def test_get_matches_by_date_returns_empty_string_when_no_ranked_games() -> None:
    client = OpenDotaApiClient(
        session=FakeSession([FakeResponse(200, [{"game_mode": 23}])]),
        max_retries=0,
        cache_path=None,
    )

    assert client.get_matches_by_date(123, 1) == ""


def test_get_heroes_returns_id_to_name_mapping() -> None:
    client = OpenDotaApiClient(
        session=FakeSession(
            [
                FakeResponse(
                    200,
                    [{"id": 1, "name": "anti-mage"}, {"id": 2, "name": "axe"}],
                )
            ]
        ),
        max_retries=0,
        cache_path=None,
    )

    assert client.get_heroes() == {1: "anti-mage", 2: "axe"}
