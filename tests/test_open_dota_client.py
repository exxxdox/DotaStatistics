from typing import Any

import pytest

from lib.open_dota_client import OpenDotaApiClient, OpenDotaApiError


class StubOpenDotaClient(OpenDotaApiClient):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.last_path = ""

    def _get(self, path: str, params=None):
        self.last_path = path
        return self.rows


def test_all_rank_leaders_build_bounded_query_and_parse_rows() -> None:
    client = StubOpenDotaClient(
        [
            {
                "id": "1",
                "localized_name": "Anti-Mage",
                **{f"{rank}_pick": 25 for rank in range(1, 9)},
                **{f"{rank}_win": 15 for rank in range(1, 9)},
            }
        ]
    )

    stats = client.get_all_rank_win_rate_leaders(top_count=10, min_games=100)

    assert stats[0].win_rate == 0.6
    assert stats[0].games == 200
    assert client.last_path == "/heroStats"


def test_all_rank_leaders_reject_invalid_limits() -> None:
    with pytest.raises(ValueError):
        StubOpenDotaClient([]).get_all_rank_win_rate_leaders(top_count=0)


def test_all_rank_leaders_reject_invalid_rows() -> None:
    with pytest.raises(OpenDotaApiError):
        StubOpenDotaClient([{"id": "bad"}]).get_all_rank_win_rate_leaders()
