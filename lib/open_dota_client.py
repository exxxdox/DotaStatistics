import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

_log = logging.getLogger(__name__)
DEFAULT_HERO_STATS_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / "res" / "monthly_hero_stats_cache.json"
)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504, 522, 524}
SECONDS_PER_WEEK = 7 * 24 * 60 * 60
RETAINED_EPOCH_WEEKS = 4


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
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        cache_path: Path | None = DEFAULT_HERO_STATS_CACHE_PATH,
        max_cache_age_seconds: int = 48 * 60 * 60,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "DotaStatistics/0.1")
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.cache_path = cache_path
        self.max_cache_age_seconds = max_cache_age_seconds
        self._sleep = sleep
        self.used_cached_hero_stats = False
        self.stats_period_start: date | None = None
        self.stats_period_end: date | None = None

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

    def get_recent_month_win_rate_leaders(
        self,
        top_count: int = 10,
        min_games: int = 100,
    ) -> list[HeroWinRateStat]:
        if top_count < 1 or min_games < 1:
            raise ValueError("top_count 和 min_games 必须大于 0")

        self.used_cached_hero_stats = False
        self._set_current_stats_period()
        try:
            # scenarios 是 OpenDota 按周维护的预聚合表；使用其四周保留窗口可
            # 避免大范围扫描 public_matches 导致 Explorer 的 Query read timeout。
            payload = self.explorer(self._recent_month_sql())
        except OpenDotaApiError:
            payload = self._load_cached_hero_stats()
            if payload is None:
                raise
            self.used_cached_hero_stats = True
            _log.warning("OpenDota 月度英雄统计不可用，使用最近一次成功缓存")

        if not isinstance(payload, list):
            raise OpenDotaApiError("OpenDota 月度英雄统计返回格式错误")

        stats = [self._parse_monthly_stat(row) for row in payload]
        if not self.used_cached_hero_stats:
            # 只有完整解析成功的数据才有资格替换最后成功缓存。
            self._save_hero_stats_cache(payload)
        eligible = [stat for stat in stats if stat.games >= min_games]
        eligible.sort(key=lambda stat: (-stat.win_rate, -stat.games, stat.hero_id))
        return eligible[:top_count]

    def get_all_rank_win_rate_leaders(
        self,
        top_count: int = 10,
        min_games: int = 100,
    ) -> list[HeroWinRateStat]:
        """兼容旧调用名称，统计口径已调整为 OpenDota 最近四个统计周。"""
        return self.get_recent_month_win_rate_leaders(top_count, min_games)

    @staticmethod
    def _recent_month_sql() -> str:
        return """
WITH bounds AS (
    SELECT FLOOR(EXTRACT(EPOCH FROM NOW()) / 604800)::int AS current_week
), monthly AS (
    SELECT
        scenarios.hero_id,
        SUM(scenarios.games)::bigint AS games,
        SUM(scenarios.wins)::bigint AS wins
    FROM scenarios
    CROSS JOIN bounds
    WHERE scenarios.item IS NULL
      AND scenarios.epoch_week >= bounds.current_week - 3
      AND scenarios.epoch_week <= bounds.current_week
    GROUP BY scenarios.hero_id
)
SELECT monthly.hero_id, heroes.localized_name, monthly.games, monthly.wins
FROM monthly
JOIN heroes ON heroes.id = monthly.hero_id
ORDER BY monthly.games DESC
""".strip()

    def _set_current_stats_period(self) -> None:
        current_week = int(time.time() // SECONDS_PER_WEEK)
        start_timestamp = (current_week - RETAINED_EPOCH_WEEKS + 1) * SECONDS_PER_WEEK
        self.stats_period_start = datetime.fromtimestamp(
            start_timestamp, tz=timezone.utc
        ).date()
        self.stats_period_end = datetime.now(tz=timezone.utc).date()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    f"{self.BASE_URL}{path}", params=params, timeout=self.timeout
                )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as error:
                status_code = getattr(error.response, "status_code", None)
                retryable = (
                    status_code is None or status_code in RETRYABLE_STATUS_CODES
                )
                if retryable and attempt < self.max_retries:
                    # 上游偶发超时很常见；短退避重试，避免瞬时故障直接影响用户。
                    self._sleep(self.retry_backoff * (2**attempt))
                    continue
                raise OpenDotaApiError(f"OpenDota 请求失败: {error}") from error
            except ValueError as error:
                raise OpenDotaApiError(f"OpenDota 响应不是有效 JSON: {error}") from error

        raise AssertionError("OpenDota 重试循环异常退出")

    def _save_hero_stats_cache(self, payload: list[dict[str, Any]]) -> None:
        if self.cache_path is None:
            return
        temporary_path = self.cache_path.with_suffix(".json.tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(
                    {
                        "cached_at": time.time(),
                        "period_start": self.stats_period_start.isoformat(),
                        "period_end": self.stats_period_end.isoformat(),
                        "data": payload,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            # 同一文件系统原子替换，避免服务中断留下半份 JSON。
            temporary_path.replace(self.cache_path)
        except OSError:
            _log.exception("写入 OpenDota heroStats 缓存失败")

    def _load_cached_hero_stats(self) -> list[dict[str, Any]] | None:
        if self.cache_path is None:
            return None
        try:
            cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
            cached_at = float(cached["cached_at"])
            payload = cached["data"]
            if time.time() - cached_at > self.max_cache_age_seconds:
                return None
            self.stats_period_start = date.fromisoformat(cached["period_start"])
            self.stats_period_end = date.fromisoformat(cached["period_end"])
            return payload if isinstance(payload, list) else None
        except (OSError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _parse_monthly_stat(row: dict[str, Any]) -> HeroWinRateStat:
        try:
            return HeroWinRateStat(
                hero_id=int(row["hero_id"]),
                hero_name=str(row["localized_name"]),
                games=int(row["games"]),
                wins=int(row["wins"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OpenDotaApiError(f"无法解析英雄胜率数据: {row}") from error
