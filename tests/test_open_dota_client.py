from typing import Any

import pytest

from lib.open_dota_client import OpenDotaApiClient, OpenDotaApiError


class StubOpenDotaClient(OpenDotaApiClient):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.last_sql = ""

    def explorer(self, sql: str) -> list[dict[str, Any]]:
        self.last_sql = sql
        return self.rows


def test_position_leaders_build_bounded_query_and_parse_rows() -> None:
    client = StubOpenDotaClient(
        [{"position": "1", "hero_id": "1", "hero_name": "Anti-Mage", "games": "20", "wins": "12"}]
    )

    stats = client.get_position_win_rate_leaders(100, 200, top_count=3, min_games=10)

    assert stats[0].win_rate == 0.6
    assert "m.start_time >= 100" in client.last_sql
    assert "m.start_time < 200" in client.last_sql
    assert "m.game_mode = 2" in client.last_sql
    assert "m.lobby_type = 1" in client.last_sql
    assert "HAVING COUNT(*) >= 10" in client.last_sql
    assert "hero_rank <= 3" in client.last_sql


def test_position_leaders_reject_invalid_range() -> None:
    with pytest.raises(ValueError):
        StubOpenDotaClient([]).get_position_win_rate_leaders(200, 100)


def test_position_leaders_reject_invalid_rows() -> None:
    with pytest.raises(OpenDotaApiError):
        StubOpenDotaClient([{"position": "bad"}]).get_position_win_rate_leaders(100, 200)
