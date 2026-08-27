import os
from dataclasses import dataclass
from typing import Any

import requests


class OpenDotaApiError(RuntimeError):
    """OpenDota 请求或响应不符合预期。"""


@dataclass(frozen=True)
class HeroWinRateStat:
    hero_id: int
    hero_name: str
    games: int
    wins: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


class OpenDotaApiClient:
    """OpenDota API 客户端，集中处理鉴权、超时和响应校验。"""

    BASE_URL = "https://api.opendota.com/api"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "DotaStatistics/0.1")

        resolved_api_key = api_key or os.environ.get("OPENDOTA_API_KEY")
        if resolved_api_key:
            # 使用文档支持的 Bearer 头，避免密钥出现在 URL 和访问日志中。
            self.session.headers["Authorization"] = f"Bearer {resolved_api_key}"

    def explorer(self, sql: str) -> list[dict[str, Any]]:
        payload = self._get("/explorer", params={"sql": sql})
        if not isinstance(payload, dict):
            raise OpenDotaApiError("OpenDota explorer 返回格式错误")
        if payload.get("err"):
            raise OpenDotaApiError(f"OpenDota explorer 查询失败: {payload['err']}")

        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise OpenDotaApiError("OpenDota explorer 响应缺少 rows")
        return rows

    def get_all_rank_win_rate_leaders(
        self,
        top_count: int = 10,
        min_games: int = 100,
    ) -> list[HeroWinRateStat]:
        if top_count < 1 or min_games < 1:
            raise ValueError("top_count 和 min_games 必须大于 0")

        payload = self._get("/heroStats")
        if not isinstance(payload, list):
            raise OpenDotaApiError("OpenDota heroStats 返回格式错误")

        # heroStats 已按 1—8 段位预聚合，在线生成时无需扫描并展开海量比赛。
        stats = [self._parse_all_rank_stat(row) for row in payload]
        eligible = [stat for stat in stats if stat.games >= min_games]
        eligible.sort(key=lambda stat: (-stat.win_rate, -stat.games, stat.hero_id))
        return eligible[:top_count]

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self.session.get(
                f"{self.BASE_URL}{path}", params=params, timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            raise OpenDotaApiError(f"OpenDota 请求失败: {error}") from error

    @staticmethod
    def _parse_all_rank_stat(row: dict[str, Any]) -> HeroWinRateStat:
        try:
            games = sum(int(row.get(f"{rank}_pick", 0)) for rank in range(1, 9))
            wins = sum(int(row.get(f"{rank}_win", 0)) for rank in range(1, 9))
            return HeroWinRateStat(
                hero_id=int(row["id"]),
                hero_name=str(row["localized_name"]),
                games=games,
                wins=wins,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OpenDotaApiError(f"无法解析英雄胜率数据: {row}") from error
