from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from lib.open_dota_client import OpenDotaApiClient, OpenDotaApiError


class StubOpenDotaClient(OpenDotaApiClient):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.last_sql = ""
        self.cache_path = None
        self.used_cached_hero_stats = False

    def explorer(self, sql: str):
        self.last_sql = sql
        return self.rows


def test_monthly_leaders_query_complete_weeks_and_parse_rows() -> None:
    client = StubOpenDotaClient(
        [
            {
                "hero_id": "1",
                "localized_name": "Anti-Mage",
                "games": "200",
                "wins": "120",
            }
        ]
    )

    stats = client.get_recent_month_win_rate_leaders(top_count=10, min_games=100)

    assert stats[0].win_rate == 0.6
    assert stats[0].games == 200
    assert "FROM scenarios" in client.last_sql
    assert "scenarios.item IS NULL" in client.last_sql
    assert "bounds.current_week - 3" in client.last_sql
    assert "scenarios.epoch_week <= bounds.current_week" in client.last_sql
    assert client.stats_period_start is not None
    assert client.stats_period_end is not None
    period_days = (client.stats_period_end - client.stats_period_start).days
    assert 21 <= period_days <= 27


def test_all_rank_leaders_reject_invalid_limits() -> None:
    with pytest.raises(ValueError):
        StubOpenDotaClient([]).get_recent_month_win_rate_leaders(top_count=0)


def test_all_rank_leaders_reject_invalid_rows() -> None:
    with pytest.raises(OpenDotaApiError):
        StubOpenDotaClient([{"hero_id": "bad"}]).get_recent_month_win_rate_leaders()


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Server Error",
                response=SimpleNamespace(status_code=self.status_code),
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


def test_hero_stats_falls_back_to_recent_cache() -> None:
    payload = [
        {
            "hero_id": 1,
            "localized_name": "Anti-Mage",
            "games": 200,
            "wins": 120,
        }
    ]
    cache_path = Path(__file__).parent / ".hero_stats_cache_test.json"
    try:
        live_client = OpenDotaApiClient(
            session=FakeSession(
                [FakeResponse(200, {"rows": payload, "err": None})]
            ),
            max_retries=0,
            cache_path=cache_path,
        )
        live_client.get_recent_month_win_rate_leaders()

        cached_client = OpenDotaApiClient(
            session=FakeSession([FakeResponse(522)]),
            max_retries=0,
            cache_path=cache_path,
        )
        stats = cached_client.get_recent_month_win_rate_leaders()

        assert stats[0].hero_name == "Anti-Mage"
        assert cached_client.used_cached_hero_stats is True
    finally:
        # 测试缓存是精确命名的临时产物，结束时不留在工作区。
        cache_path.unlink(missing_ok=True)
        cache_path.with_suffix(".json.tmp").unlink(missing_ok=True)
