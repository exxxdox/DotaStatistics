import os
from dataclasses import dataclass
from typing import Any

import requests


class OpenDotaApiError(RuntimeError):
    """OpenDota 请求或响应不符合预期。"""


@dataclass(frozen=True)
class HeroPositionStat:
    position: int
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

    def get_position_win_rate_leaders(
        self,
        start_timestamp: int,
        end_timestamp: int,
        top_count: int = 3,
        min_games: int = 3,
    ) -> list[HeroPositionStat]:
        if start_timestamp >= end_timestamp:
            raise ValueError("统计开始时间必须早于结束时间")
        if top_count < 1 or min_games < 1:
            raise ValueError("top_count 和 min_games 必须大于 0")

        # OpenDota 没有 1-5 号位字段；按同队 GPM 排名还原经济优先级。
        # Explorer 的近期样本是职业比赛，因此限定队长模式并排除残缺数据。
        sql = f"""
WITH eligible_matches AS (
    SELECT m.match_id, m.radiant_win
    FROM matches AS m
    JOIN player_matches AS pm USING (match_id)
    WHERE m.start_time >= {int(start_timestamp)}
      AND m.start_time < {int(end_timestamp)}
      AND m.game_mode = 2
      AND m.lobby_type = 1
    GROUP BY m.match_id, m.radiant_win
    HAVING COUNT(*) = 10
       AND COUNT(pm.gold_per_min) = 10
       AND BOOL_AND(COALESCE(pm.leaver_status, 0) = 0)
), positioned_players AS (
    SELECT
        pm.hero_id,
        h.localized_name AS hero_name,
        ROW_NUMBER() OVER (
            PARTITION BY pm.match_id, (pm.player_slot < 128)
            ORDER BY pm.gold_per_min DESC, pm.player_slot
        ) AS position,
        ((pm.player_slot < 128) = em.radiant_win) AS won
    FROM player_matches AS pm
    JOIN eligible_matches AS em USING (match_id)
    JOIN heroes AS h ON h.id = pm.hero_id
), aggregated AS (
    SELECT
        position,
        hero_id,
        hero_name,
        COUNT(*) AS games,
        COUNT(*) FILTER (WHERE won) AS wins
    FROM positioned_players
    GROUP BY position, hero_id, hero_name
    HAVING COUNT(*) >= {int(min_games)}
), ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY position
            ORDER BY wins::numeric / games DESC, games DESC, hero_id
        ) AS hero_rank
    FROM aggregated
)
SELECT position, hero_id, hero_name, games, wins
FROM ranked
WHERE hero_rank <= {int(top_count)}
ORDER BY position, hero_rank
""".strip()

        return [self._parse_position_stat(row) for row in self.explorer(sql)]

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
    def _parse_position_stat(row: dict[str, Any]) -> HeroPositionStat:
        try:
            return HeroPositionStat(
                position=int(row["position"]),
                hero_id=int(row["hero_id"]),
                hero_name=str(row["hero_name"]),
                games=int(row["games"]),
                wins=int(row["wins"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OpenDotaApiError(f"无法解析位置胜率数据: {row}") from error
