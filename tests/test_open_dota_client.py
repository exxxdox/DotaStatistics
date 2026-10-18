import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import Any

import pytest
import requests

from lib.open_dota_client import (
    OpenDotaApiClient,
    OpenDotaApiError,
    _CachePersistenceError,
)


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
        self.requests: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def get(self, *args, **kwargs) -> FakeResponse:
        self.requests.append((args, kwargs))
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


def test_daily_cache_survives_interrupted_backfill(tmp_path: Path) -> None:
    fixed_now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    cache_path = tmp_path / "hero_stats.json"
    client = OpenDotaApiClient(cache_path=cache_path, now=lambda: fixed_now)
    client._set_current_stats_period()
    dates = client._target_dates()
    rows = [{"hero_id": 1, "games": 10, "wins": 6}]
    snapshot = {
        "cached_at": fixed_now.timestamp(),
        "period_start": "2026-07-27",
        "period_end": "2026-08-25",
        "data": [{"hero_id": 1, "games": 300, "wins": 180}],
    }
    client._save_cache_document({
        "version": 2,
        "days": {day.isoformat(): rows for day in dates[:-2]},
        "snapshot": snapshot,
    })
    saved = Event()
    blocked = Event()
    release = Event()
    original_save = client._save_cache_document

    def save(document: dict[str, Any]) -> None:
        original_save(document)
        saved.set()

    def fetch(day: date) -> list[dict[str, Any]]:
        if day == dates[-2]:
            return rows
        blocked.set()
        assert release.wait(5), "测试必须释放阻塞请求"
        raise RuntimeError("模拟刷新意外中断")

    client._save_cache_document = save  # type: ignore[method-assign]
    client._fetch_daily_stats = fetch  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.get_recent_month_win_rate_leaders)
        try:
            assert blocked.wait(5)
            assert saved.wait(5), "后续请求仍阻塞时，成功日必须已落盘"
            document = json.loads(cache_path.read_text(encoding="utf-8"))
            assert dates[-2].isoformat() in document["days"]
            assert dates[-1].isoformat() not in document["days"]
            assert document["snapshot"] == snapshot
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="模拟刷新意外中断"):
            future.result(timeout=5)

    resumed = OpenDotaApiClient(cache_path=cache_path, now=lambda: fixed_now)
    calls: list[date] = []

    def resume_fetch(day: date) -> list[dict[str, Any]]:
        calls.append(day)
        return rows

    resumed._fetch_daily_stats = resume_fetch  # type: ignore[method-assign]
    assert resumed.get_recent_month_win_rate_leaders()[0].games == 300
    assert calls == [dates[-1]]
    assert resumed._load_cache_document()["snapshot"]["period_end"] == "2026-08-26"


@pytest.mark.parametrize("workers", [1, 3])
def test_daily_checkpoint_rejects_failed_and_invalid_days(
    tmp_path: Path, workers: int,
) -> None:
    fixed_now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    client = OpenDotaApiClient(
        cache_path=tmp_path / "hero_stats.json",
        refresh_workers=workers,
        now=lambda: fixed_now,
    )
    client._set_current_stats_period()
    dates = client._target_dates()
    rows = [{"hero_id": 1, "games": 10, "wins": 6}]
    client._save_cache_document({
        "version": 2,
        "days": {day.isoformat(): rows for day in dates[:-3]},
        "snapshot": None,
    })

    def fetch(day: date) -> list[dict[str, Any]]:
        if day == dates[-3]:
            return rows
        if day == dates[-2]:
            return [{"hero_id": 1, "games": 5, "wins": 6}]
        raise OpenDotaApiError("查询失败")

    client._fetch_daily_stats = fetch  # type: ignore[method-assign]
    with pytest.raises(OpenDotaApiError):
        client.get_recent_month_win_rate_leaders()

    document = client._load_cache_document()
    assert len(document["days"]) == 28
    assert document["days"][dates[-3].isoformat()] == rows
    assert all(day.isoformat() not in document["days"] for day in dates[-2:])
    assert document["snapshot"] is None


def test_monthly_stats_reject_invalid_limits_and_rows() -> None:
    client = OpenDotaApiClient(cache_path=None)
    with pytest.raises(ValueError):
        client.get_recent_month_win_rate_leaders(top_count=0)
    with pytest.raises(OpenDotaApiError):
        client._aggregate_monthly_stats([{"hero_id": 1, "games": 5, "wins": 6}])


def test_cold_cache_stops_after_first_refresh_error() -> None:
    client = OpenDotaApiClient(cache_path=None, refresh_workers=1)
    calls: list[date] = []

    def fail(day: date) -> list[dict[str, Any]]:
        calls.append(day)
        raise OpenDotaApiError("查询失败")

    client._fetch_daily_stats = fail  # type: ignore[method-assign]

    with pytest.raises(OpenDotaApiError, match="查询失败"):
        client.get_recent_month_win_rate_leaders()

    assert len(calls) == 1


def test_cache_write_failure_is_reported(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache-as-directory"
    cache_path.mkdir()
    client = OpenDotaApiClient(cache_path=cache_path)

    with pytest.raises(OpenDotaApiError, match="写入 OpenDota 英雄日统计缓存失败"):
        client._save_cache_document({"version": 2, "days": {}, "snapshot": None})


def test_invalid_cached_day_is_refetched_or_falls_back_to_snapshot(
    tmp_path: Path,
) -> None:
    fixed_now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    cache_path = tmp_path / "hero_stats.json"
    client = OpenDotaApiClient(cache_path=cache_path, now=lambda: fixed_now)
    client._set_current_stats_period()
    dates = client._target_dates()
    rows = [{"hero_id": 1, "games": 10, "wins": 6}]
    bad_day = dates[-1]
    cache_path.write_text(
        json.dumps(
            {
                "version": 2,
                "days": {
                    **{day.isoformat(): rows for day in dates[:-1]},
                    bad_day.isoformat(): [
                        {"hero_id": 1, "games": 5, "wins": 6}
                    ],
                },
                "snapshot": {
                    "cached_at": fixed_now.timestamp(),
                    "period_start": dates[0].isoformat(),
                    "period_end": dates[-1].isoformat(),
                    "data": [{"hero_id": 1, "games": 300, "wins": 180}],
                },
            }
        ),
        encoding="utf-8",
    )
    calls: list[date] = []

    def fail(day: date) -> list[dict[str, Any]]:
        calls.append(day)
        raise OpenDotaApiError("查询失败")

    client._fetch_daily_stats = fail  # type: ignore[method-assign]

    stats = client.get_recent_month_win_rate_leaders(min_games=1)

    assert calls == [bad_day]
    assert stats[0].games == 300
    assert client.used_cached_hero_stats is True


def test_daily_checkpoint_write_failure_is_not_hidden_by_snapshot(tmp_path: Path) -> None:
    fixed_now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    cache_path = tmp_path / "hero_stats.json"
    client = OpenDotaApiClient(cache_path=cache_path, now=lambda: fixed_now)
    client._set_current_stats_period()
    dates = client._target_dates()
    rows = [{"hero_id": 1, "games": 10, "wins": 6}]
    cache_path.write_text(
        json.dumps(
            {
                "version": 2,
                "days": {day.isoformat(): rows for day in dates[:-1]},
                "snapshot": {
                    "cached_at": fixed_now.timestamp(),
                    "period_start": dates[0].isoformat(),
                    "period_end": dates[-1].isoformat(),
                    "data": [{"hero_id": 1, "games": 300, "wins": 180}],
                },
            }
        ),
        encoding="utf-8",
    )
    client._fetch_daily_stats = lambda _day: rows  # type: ignore[method-assign]

    def fail_save(_document: dict[str, Any]) -> None:
        raise _CachePersistenceError("写入失败")

    client._save_cache_document = fail_save  # type: ignore[method-assign]

    with pytest.raises(OpenDotaApiError, match="写入失败"):
        client.get_recent_month_win_rate_leaders(min_games=1)


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


def test_recent_matches_default_returns_five_ranked_games() -> None:
    ranked = {
        "lobby_type": 7,
        "game_mode": 23,
        "hero_id": 1,
        "radiant_win": True,
        "player_slot": 0,
    }
    client = OpenDotaApiClient(
        session=FakeSession(
            [FakeResponse(200, [{"lobby_type": 0, "game_mode": 22}] + [ranked] * 6)]
        ),
        max_retries=0,
        cache_path=None,
    )
    assert client.get_recent_matches(123).count("英雄:英雄 1") == 5


def test_get_recent_matches_filters_non_ranked_and_limits() -> None:
    recent = [
        {
            "lobby_type": 7,
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
        {"lobby_type": 0, "hero_id": 2},  # 非天梯，应被过滤
        {
            "lobby_type": 7,
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
            "lobby_type": 7,
            "match_id": 9,
            "radiant_win": True,
            "player_slot": 0,
            "hero_id": 1,
            "kills": 5,
            "deaths": 3,
            "assists": 2,
            "duration": 3000,
            "gold_per_min": 500,
            "xp_per_min": 400,
            "hero_damage": 900,
            "tower_damage": 100,
            "hero_healing": 0,
        }
    ]
    client = OpenDotaApiClient(
        session=FakeSession([FakeResponse(200, matches)]),
        max_retries=0,
        cache_path=None,
        hero_name_resolver=lambda hero_id: "敌法师" if hero_id == 1 else None,
    )

    result = client.get_matches_by_date(123, 1)

    assert "游戏胜利 英雄:敌法师 击杀:5 死亡:3 助攻:2" in result
    assert "持续时间3000秒" in result
    assert "此玩家数据结束" in result
    assert client.session.calls == 1


def test_get_matches_by_date_returns_empty_string_when_no_ranked_games() -> None:
    client = OpenDotaApiClient(
        session=FakeSession([FakeResponse(200, [{"lobby_type": 0}])]),
        max_retries=0,
        cache_path=None,
    )

    assert client.get_matches_by_date(123, 1) == ""


def test_get_matches_by_date_returns_none_on_api_error() -> None:
    client = OpenDotaApiClient(
        session=FakeSession([FakeResponse(522)]),
        max_retries=0,
        cache_path=None,
    )

    assert client.get_matches_by_date(123, 1) is None


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


def test_agent_player_profile_uses_fixed_endpoint_and_removes_private_links() -> None:
    session = FakeSession([FakeResponse(200, {
        "rank_tier": 75,
        "leaderboard_rank": 42,
        "profile": {
            "account_id": 123,
            "personaname": "A" * 200 + "\u200b\n隐藏",
            "loccountrycode": "CN",
            "steamid": "private-steam-id",
            "avatarfull": "https://example.invalid/avatar",
            "profileurl": "https://example.invalid/profile",
        },
    })])
    client = OpenDotaApiClient(session=session, max_retries=2, cache_path=None)

    result = client.get_player_data(123, "profile", days=30, limit=10)

    assert result["rank_tier"] == 75
    assert result["profile"] == {
        "account_id": 123,
        "personaname": "A" * 160,
        "loccountrycode": "CN",
    }
    assert session.requests == [((f"{client.BASE_URL}/players/123",), {
        "params": None,
        "timeout": 10.0,
    })]


@pytest.mark.parametrize(
    ("view", "endpoint", "params", "payload", "expected"),
    [
        ("win_loss", "wl", {"date": 30, "limit": 2}, {"win": 7, "lose": 3, "x": 1}, {"win": 7, "lose": 3}),
        ("heroes", "heroes", {"date": 30, "limit": 2}, [{"hero_id": 1, "games": 9, "img": "x"}] * 3, [{"hero_id": 1, "games": 9}] * 2),
        ("peers", "peers", {"date": 30, "limit": 2}, [{"account_id": 8, "personaname": "P", "avatar": "x"}], [{"account_id": 8, "personaname": "P"}]),
        ("totals", "totals", {"date": 30, "limit": 2}, [{"field": "kills", "n": 3, "sum": 9, "other": 1}], [{"field": "kills", "n": 3, "sum": 9}]),
        ("ratings", "ratings", None, [{"rank_tier": 70, "time": 1, "steamid": "x"}], [{"rank_tier": 70, "time": 1}]),
        ("rankings", "rankings", None, [{"hero_id": 2, "score": 4, "avatar": "x"}], [{"hero_id": 2, "score": 4}]),
    ],
)
def test_agent_player_views_use_exact_paths_params_and_projection(
    view: str,
    endpoint: str,
    params: dict[str, int] | None,
    payload: Any,
    expected: Any,
) -> None:
    session = FakeSession([FakeResponse(200, payload)])
    client = OpenDotaApiClient(session=session, cache_path=None)

    assert client.get_player_data(123, view, days=30, limit=2) == expected
    assert session.requests[0] == ((
        f"{client.BASE_URL}/players/123/{endpoint}",
    ), {"params": params, "timeout": 10.0})


def test_agent_player_counts_bounds_categories_and_entries() -> None:
    session = FakeSession([FakeResponse(200, {
        "game_mode": {
            "1": {"games": 10, "win": 6, "unsafe": "drop"},
            "2": {"games": 8, "win": 4},
            "3": {"games": 6, "win": 3},
        },
        "region": {"1": 4},
        "wordcloud": {"unsafe": 99},
    })])
    client = OpenDotaApiClient(session=session, cache_path=None)

    result = client.get_player_data(123, "counts", days=7, limit=2)

    assert result == {
        "game_mode": {
            "1": {"games": 10, "win": 6},
            "2": {"games": 8, "win": 4},
        },
        "region": {"1": 4},
    }
    assert session.requests[0][1]["params"] == {"date": 7, "limit": 2}


def test_agent_match_query_projects_core_fields_and_ten_players() -> None:
    players = [
        {
            "account_id": index,
            "hero_id": index + 1,
            "kills": index,
            "personaname": "hidden",
            "chat": [{"key": "hidden"}],
        }
        for index in range(12)
    ]
    session = FakeSession([FakeResponse(200, {
        "match_id": 99,
        "duration": 3000,
        "league_name": "L" * 200,
        "players": players,
        "chat": [{"key": "hidden"}],
    })])
    client = OpenDotaApiClient(session=session, cache_path=None)

    result = client.get_match_data(99)

    assert result["league_name"] == "L" * 160
    assert len(result["players"]) == 10
    assert result["players"][0] == {"account_id": 0, "hero_id": 1, "kills": 0}
    assert "chat" not in result
    assert session.requests[0] == ((f"{client.BASE_URL}/matches/99",), {
        "params": None,
        "timeout": 10.0,
    })


@pytest.mark.parametrize(
    ("view", "path", "params", "payload", "expected"),
    [
        ("matchups", "/heroes/7/matchups", None, [{"hero_id": 2, "games_played": 4, "wins": 3, "x": 1}], [{"hero_id": 2, "games_played": 4, "wins": 3}]),
        ("durations", "/heroes/7/durations", None, [{"duration_bin": "30", "games_played": 4, "wins": 2}], [{"duration_bin": "30", "games_played": 4, "wins": 2}]),
        ("recent_matches", "/heroes/7/matches", None, [{"match_id": 4, "radiant_name": "A", "radiant_logo": "x"}], [{"match_id": 4, "radiant_name": "A"}]),
        ("rankings", "/rankings", {"hero_id": 7}, {"hero_id": 7, "rankings": [{"account_id": 3, "score": 2, "steamid": "x"}]}, {"hero_id": 7, "rankings": [{"account_id": 3, "score": 2}]}),
    ],
)
def test_agent_hero_views_use_fixed_endpoints(
    view: str,
    path: str,
    params: dict[str, int] | None,
    payload: Any,
    expected: Any,
) -> None:
    session = FakeSession([FakeResponse(200, payload)])
    client = OpenDotaApiClient(session=session, cache_path=None)

    assert client.get_hero_data(7, view, limit=2) == expected
    assert session.requests[0] == ((f"{client.BASE_URL}{path}",), {
        "params": params,
        "timeout": 10.0,
    })


@pytest.mark.parametrize(
    ("view", "path", "payload", "expected"),
    [
        ("overview", "/teams/5", {"team_id": 5, "name": "Team", "logo_url": "x"}, {"team_id": 5, "name": "Team"}),
        ("matches", "/teams/5/matches", [{"match_id": 1, "opposing_team_name": "Other", "logo": "x"}], [{"match_id": 1, "opposing_team_name": "Other"}]),
        ("players", "/teams/5/players", [{"account_id": 2, "name": "Player", "steamid": "x"}], [{"account_id": 2, "name": "Player"}]),
        ("heroes", "/teams/5/heroes", [{"hero_id": 3, "games_played": 2, "img": "x"}], [{"hero_id": 3, "games_played": 2}]),
    ],
)
def test_agent_team_views_use_fixed_endpoints(
    view: str, path: str, payload: Any, expected: Any
) -> None:
    session = FakeSession([FakeResponse(200, payload)])
    client = OpenDotaApiClient(session=session, cache_path=None)

    assert client.get_team_data(5, view, limit=2) == expected
    assert session.requests[0] == ((f"{client.BASE_URL}{path}",), {
        "params": None,
        "timeout": 10.0,
    })


@pytest.mark.parametrize(
    ("view", "path", "payload", "expected"),
    [
        ("live_matches", "/live", [{"match_id": 1, "team_name_radiant": "A", "team_logo_radiant": "x"}], [{"match_id": 1, "team_name_radiant": "A"}]),
        ("pro_matches", "/proMatches", [{"match_id": 2, "radiant_name": "A", "radiant_logo": "x"}], [{"match_id": 2, "radiant_name": "A"}]),
        ("public_matches", "/publicMatches", [{"match_id": 3, "radiant_team": [1, 2]}], [{"match_id": 3}]),
        ("top_players", "/playersByRank", [{"account_id": 4, "personaname": "P", "avatar": "x"}], [{"account_id": 4, "personaname": "P"}]),
        ("hero_stats", "/heroStats", [{"hero_id": 5, "localized_name": "Hero", "img": "x"}], [{"hero_id": 5, "localized_name": "Hero"}]),
        ("pro_players", "/proPlayers", [{"account_id": 6, "team_name": "T", "steamid": "x"}], [{"account_id": 6, "team_name": "T"}]),
    ],
)
def test_agent_global_views_use_fixed_endpoints_and_limit_rows(
    view: str, path: str, payload: Any, expected: Any
) -> None:
    session = FakeSession([FakeResponse(200, payload * 3)])
    client = OpenDotaApiClient(session=session, cache_path=None)

    assert client.get_global_data(view, limit=1) == expected
    assert session.requests[0] == ((f"{client.BASE_URL}{path}",), {
        "params": None,
        "timeout": 10.0,
    })


@pytest.mark.parametrize(
    ("method", "args", "kwargs"),
    [
        ("get_player_data", (True, "profile"), {"days": 1, "limit": 1}),
        ("get_player_data", (1, "unknown"), {"days": 1, "limit": 1}),
        ("get_player_data", (1, "profile"), {"days": 0, "limit": 1}),
        ("get_player_data", (1, "profile"), {"days": 1, "limit": 21}),
        ("get_match_data", (False,), {}),
        ("get_hero_data", (0, "matchups"), {"limit": 1}),
        ("get_hero_data", (1, "unknown"), {"limit": 1}),
        ("get_team_data", (1, "overview"), {"limit": True}),
        ("get_team_data", (1, "unknown"), {"limit": 1}),
        ("get_global_data", ("unknown",), {"limit": 1}),
    ],
)
def test_agent_queries_reject_invalid_values_before_network(
    method: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    session = FakeSession([])
    client = OpenDotaApiClient(session=session, cache_path=None)

    with pytest.raises(ValueError):
        getattr(client, method)(*args, **kwargs)

    assert session.calls == 0


def test_agent_query_disables_retry_even_when_client_would_retry() -> None:
    session = FakeSession([FakeResponse(522), FakeResponse(200, [])])
    client = OpenDotaApiClient(session=session, max_retries=2, cache_path=None)

    with pytest.raises(OpenDotaApiError):
        client.get_global_data("live_matches", limit=1)

    assert session.calls == 1
